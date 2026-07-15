import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import type { ImageContent, Model } from "@earendil-works/pi-ai";
import {
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  type ExtensionAPI,
  getAgentDir,
  ModelRegistry,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { AdbDevice, type AdbOptions } from "./adb.js";
import { SYSTEM_PROMPT } from "./prompt.js";
import { createAndroidTools, type AgentState } from "./tools.js";
import { LearningStore, summarizeTrajectory } from "./learning.js";

export interface RunOptions extends AdbOptions {
  provider?: string;
  model?: string;
  thinking?: "off" | "minimal" | "low" | "medium" | "high" | "xhigh";
  maxActions?: number;
  settleMs?: number;
  sessionDir?: string;
  learning?: boolean;
  learningRoot?: string;
  onText?: (text: string) => void;
  onLearningError?: (error: unknown) => void;
}

function initialImage(data: Buffer): ImageContent {
  return { type: "image", data: data.toString("base64"), mimeType: "image/png" };
}

export async function runTask(task: string, options: RunOptions = {}): Promise<AgentState> {
  const cwd = process.cwd();
  const agentDir = getAgentDir();
  const sessionDir = resolve(options.sessionDir ?? "runs");
  await mkdir(sessionDir, { recursive: true });
  const learning = new LearningStore(options.learningRoot);
  await learning.initialize();

  const authStorage = AuthStorage.create();
  const modelRegistry = ModelRegistry.create(authStorage);
  let model: Model<any> | undefined;
  if (options.provider && options.model) {
    model = modelRegistry.find(options.provider, options.model);
    if (!model) throw new Error(`Unknown model ${options.provider}/${options.model}`);
  }

  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: true },
    retry: { enabled: true, maxRetries: 2 },
  });
  const device = new AdbDevice(options);
  const state: AgentState = { actions: 0, finished: false };
  const androidTools = createAndroidTools(device, state, options);
  const memoryPrompt = await learning.memoryPrompt();
  const adbPrompt = options.serial
    ? `ADB target for this task: executable ${JSON.stringify(device.adbPath)}, serial ${JSON.stringify(options.serial)}. Include -s ${JSON.stringify(options.serial)} in direct adb commands.`
    : `ADB executable for this task: ${JSON.stringify(device.adbPath)}. No serial was specified; direct adb commands may use the single connected device.`;
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager,
    noExtensions: true,
    additionalSkillPaths: [learning.skillsDir],
    noPromptTemplates: true,
    noContextFiles: true,
    systemPrompt: SYSTEM_PROMPT,
    appendSystemPrompt: [adbPrompt, ...(memoryPrompt ? [memoryPrompt] : [])],
    extensionFactories: [(pi) => registerAndroidToolLoader(pi, androidTools)],
  });
  await resourceLoader.reload();

  const { session } = await createAgentSession({
    cwd,
    model,
    thinkingLevel: options.thinking ?? "medium",
    resourceLoader,
    sessionManager: SessionManager.create(cwd, sessionDir),
    settingsManager,
    authStorage,
    modelRegistry,
  });

  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
      options.onText?.(event.assistantMessageEvent.delta);
    }
  });

  try {
    await session.prompt(`Task: ${task}\n\nThe attached image is the current phone screen.`, {
      images: [initialImage(await device.screenshot())],
    });
    if (options.learning !== false && session.model) {
      try {
        await learning.review(summarizeTrajectory(session.messages), {
          model: session.model,
          authStorage,
          modelRegistry,
          settingsManager,
        });
      } catch (error) {
        options.onLearningError?.(error);
      }
    }
    return state;
  } finally {
    try {
      await device.restoreInputMethod();
    } finally {
      unsubscribe();
      session.dispose();
    }
  }
}

const TOOL_ALIASES: Record<string, string> = {
  observe: "observe inspect screen screenshot view 查看 观察 屏幕 截图",
  tap: "tap click press button 点击 轻触 按钮",
  long_press: "long press hold 长按",
  swipe: "swipe scroll drag 滑动 滚动 拖动",
  type_text: "type text input enter keyboard 输入 文字 键盘",
  system_button: "system back home enter 系统 返回 主页 回车",
  open_app: "open launch app application browser 打开 启动 应用 浏览器",
  wait: "wait loading animation 等待 加载",
  answer: "answer respond result information 回答 答案 信息",
  finish: "finish complete done terminate 完成 结束",
};

function registerAndroidToolLoader(pi: ExtensionAPI, androidTools: ReturnType<typeof createAndroidTools>): void {
  const names = new Set(androidTools.map((tool) => tool.name));
  const lifecycleTools = new Set(["answer", "finish"]);
  const searchableToolNames = [...names].filter((name) => !lifecycleTools.has(name));
  const searchableNames = new Set(searchableToolNames);
  const searchableToolList = searchableToolNames.join(", ");
  androidTools.forEach((tool) => pi.registerTool(tool));

  pi.registerTool({
    name: "search_tools",
    label: "Search tools",
    description: `Search for and enable atomic Android GUI tools. Searchable tools: ${searchableToolList}. Search by tool name or primitive capability; domain-specific operations are not tools.`,
    promptSnippet: `Search and load an atomic Android GUI tool when phone interaction is needed. Available tools: ${searchableToolList}.`,
    parameters: Type.Object({
      query: Type.String({ description: `Tool name or primitive capability to find. Search scope: ${searchableToolList}` }),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
    }),
    async execute(_id, { query, limit }) {
      const terms = query.toLowerCase().split(/[^\p{L}\p{N}_]+/u).filter(Boolean);
      const matches = androidTools
        .filter((tool) => searchableNames.has(tool.name))
        .map((tool) => {
          const haystack = `${tool.name} ${tool.description} ${TOOL_ALIASES[tool.name] ?? ""}`.toLowerCase();
          return { name: tool.name, score: terms.filter((term) => haystack.includes(term)).length };
        })
        .filter(({ score }) => score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit ?? 4)
        .map(({ name }) => name);

      if (matches.length === 0) {
        return {
          content: [{ type: "text", text: `No Android GUI tools found for: ${query}. Search by one of these tool names or its primitive capability: ${searchableToolList}.` }],
          details: { matches: [] as string[], added: [] as string[] },
        };
      }
      const active = pi.getActiveTools();
      const added = matches.filter((name) => !active.includes(name));
      pi.setActiveTools([...new Set([...active, ...added])]);
      return {
        content: [{ type: "text", text: added.length ? `Loaded tools: ${added.join(", ")}` : `Already loaded: ${matches.join(", ")}` }],
        details: { matches, added },
      };
    },
  });

  pi.on("session_start", () => {
    const builtins = pi.getActiveTools().filter((name) => !names.has(name));
    pi.setActiveTools([...new Set([...builtins, "search_tools", ...lifecycleTools])]);
  });
}
