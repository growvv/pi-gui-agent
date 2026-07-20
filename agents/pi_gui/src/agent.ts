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
  maxNoProgressActions?: number;
  maxModelTokens?: number;
  sessionDir?: string;
  ledgerDir?: string;
  learning?: boolean;
  learningRoot?: string;
  appMap?: Record<string, string>;
  onText?: (text: string) => void;
  onLearningError?: (error: unknown) => void;
  onLearningReview?: (status: "saved" | "skipped", writes: string[]) => void;
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
    if (options.maxModelTokens) model = { ...model, maxTokens: options.maxModelTokens };
  }

  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: true },
  });
  const device = new AdbDevice(options);
  const state: AgentState = { actions: 0, finished: false };
  const androidTools = createAndroidTools(device, state, {
    ...options,
    ledgerRequired: true,
    originalTask: task,
    ledgerDir: options.ledgerDir,
    screenshotArchiveDir: resolve(sessionDir, "screenshots"),
  });
  const memoryPrompt = await learning.memoryPrompt();
  const adbPrompt = options.serial
    ? `ADB target for this task: executable ${JSON.stringify(device.adbPath)}, serial ${JSON.stringify(options.serial)}. Include -s ${JSON.stringify(options.serial)} in direct adb commands.`
    : `ADB executable for this task: ${JSON.stringify(device.adbPath)}. No serial was specified; direct adb commands may use the single connected device.`;
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager,
    noExtensions: true,
    additionalSkillPaths: [resolve(cwd, "skills"), learning.skillsDir],
    noPromptTemplates: true,
    noContextFiles: true,
    systemPrompt: SYSTEM_PROMPT,
    appendSystemPrompt: [adbPrompt, ...(memoryPrompt ? [memoryPrompt] : [])],
    extensionFactories: [(pi) => registerAndroidTools(pi, androidTools, state)],
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
    try {
      await session.prompt(`Task: ${task}\n\nThe attached image is the current phone screen.`, {
        images: [initialImage(await device.screenshot())],
      });
    } catch (error) {
      if (!state.finished && !state.stalled) throw error;
    }
    if (options.learning !== false && session.model) {
      try {
        const review = await learning.review(summarizeTrajectory(session.messages), {
          finished: state.finished,
          stalled: state.stalled ?? false,
          actions: state.actions,
          progressWarnings: state.progressWarnings ?? 0,
        }, {
          model: session.model,
          authStorage,
          modelRegistry,
          settingsManager,
        });
        options.onLearningReview?.(review.status, review.writes);
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

export function shouldAbortAfterToolResult(
  state: Pick<AgentState, "finished" | "stalled">,
): boolean {
  return state.finished || Boolean(state.stalled);
}

function registerAndroidTools(pi: ExtensionAPI, androidTools: ReturnType<typeof createAndroidTools>, state: AgentState): void {
  const names = androidTools.map((tool) => tool.name);
  androidTools.forEach((tool) => pi.registerTool(tool));

  pi.on("session_start", () => {
    pi.setActiveTools([...new Set([...pi.getActiveTools(), ...names])]);
  });

  pi.on("tool_result", (event, context) => {
    if (shouldAbortAfterToolResult(state)) context.abort();
  });

  // Keep only the newest screenshot in provider context. Older images remain
  // available in the session screenshots archive and are represented by the
  // preceding tool result's fingerprint, timestamp, and visible UI text.
  pi.on("context", (event) => {
    let newestImage: unknown;
    for (let i = event.messages.length - 1; i >= 0 && !newestImage; i--) {
      const message = event.messages[i] as { content?: unknown } | undefined;
      if (!Array.isArray(message?.content)) continue;
      for (let j = message.content.length - 1; j >= 0; j--) {
        const part = message.content[j] as { type?: string } | undefined;
        if (part?.type === "image") {
          newestImage = part;
          break;
        }
      }
    }
    for (const message of event.messages as Array<{ content?: unknown }>) {
      if (!Array.isArray(message.content)) continue;
      message.content = message.content.flatMap((part) => {
        const item = part as { type?: string };
        if (item.type !== "image" || item === newestImage) return [part];
        return [{
          type: "text",
          text: "[Historical screenshot omitted from provider context; fingerprint/time/UI text and PNG archive remain available in the preceding tool result.]",
        }];
      });
    }
  });

}
