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
import { HttpDevice } from "./http.js";
import { systemPrompt } from "./prompt.js";
import { createAndroidTools, type AgentState } from "./tools.js";
import { LearningStore, summarizeTrajectory } from "./learning.js";

export interface RunOptions extends AdbOptions {
  serverUrl?: string;
  provider?: string;
  model?: string;
  thinking?: "off" | "minimal" | "low" | "medium" | "high" | "xhigh";
  maxActions?: number;
  maxSteps?: number;
  settleMs?: number;
  maxNoProgressActions?: number;
  maxModelTokens?: number;
  sessionDir?: string;
  ledgerDir?: string;
  disableLedgerTool?: boolean;
  learning?: boolean;
  learningRoot?: string;
  appMap?: Record<string, string>;
  onText?: (text: string) => void;
  onLearningError?: (error: unknown) => void;
  onLearningReview?: (status: "saved" | "skipped", writes: string[]) => void;
}

export const DEFAULT_MAX_STEPS = 100;

/** Match the report's "Thinking & action" event definition. */
export function isThinkingActionMessage(message: unknown): boolean {
  if (!message || typeof message !== "object") return false;
  const value = message as { role?: unknown; content?: unknown };
  if (value.role !== "assistant") return false;
  if (typeof value.content === "string") return value.content.length > 0;
  if (!Array.isArray(value.content)) return false;
  return value.content.some((part) => {
    if (!part || typeof part !== "object") return false;
    const item = part as { type?: unknown; text?: unknown; thinking?: unknown };
    if (item.type === "toolCall" || item.type === "tool_use") return true;
    if (item.type === "text") return typeof item.text === "string" && item.text.length > 0;
    if (item.type === "thinking") {
      return typeof item.thinking === "string" && item.thinking.length > 0;
    }
    return false;
  });
}

export function recordThinkingActionStep(state: AgentState, message: unknown): boolean {
  if (state.aborted || !isThinkingActionMessage(message)) return false;
  state.steps = (state.steps ?? 0) + 1;
  return true;
}

export function abortIfStepLimitReached(state: AgentState, maxSteps: number): boolean {
  if (state.finished || state.stalled || state.aborted || (state.steps ?? 0) < maxSteps) {
    return false;
  }
  state.aborted = true;
  state.abortReason = `Step limit reached: maximum ${maxSteps} Thinking & action steps.`;
  return true;
}

function initialImage(data: Buffer): ImageContent {
  return { type: "image", data: data.toString("base64"), mimeType: "image/png" };
}

const MULTIMODAL_RETRY_LIMIT = 2;

export function latestMultimodalProviderError(messages: readonly unknown[]): string | undefined {
  const message = messages.at(-1) as { role?: string; stopReason?: string; errorMessage?: string } | undefined;
  if (message?.role !== "assistant" || message.stopReason !== "error") return undefined;
  return /multimodal data is corrupted or cannot be processed/i.test(message.errorMessage ?? "")
    ? message.errorMessage
    : undefined;
}

export function finalAssistantText(messages: readonly unknown[]): string | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index] as { role?: unknown; content?: unknown } | undefined;
    if (message?.role !== "assistant") continue;
    if (typeof message.content === "string") return message.content.trim() || undefined;
    if (!Array.isArray(message.content)) return undefined;
    const text = message.content.flatMap((part) => {
      if (!part || typeof part !== "object") return [];
      const value = part as { type?: unknown; text?: unknown };
      return value.type === "text" && typeof value.text === "string" ? [value.text] : [];
    }).join("\n").trim();
    return text || undefined;
  }
  return undefined;
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
  const device = options.serverUrl
    ? new HttpDevice({ serverUrl: options.serverUrl })
    : new AdbDevice(options);
  const maxSteps = options.maxSteps ?? DEFAULT_MAX_STEPS;
  if (!Number.isInteger(maxSteps) || maxSteps <= 0) {
    throw new Error("maxSteps must be a positive integer");
  }
  const state: AgentState = { actions: 0, steps: 0, finished: false };
  const ledgerEnabled = options.disableLedgerTool !== true;
  const androidTools = createAndroidTools(device, state, {
    ...options,
    ledgerEnabled,
    ledgerRequired: ledgerEnabled,
    originalTask: task,
    ledgerDir: options.ledgerDir,
    screenshotArchiveDir: resolve(sessionDir, "screenshots"),
  });
  const memoryPrompt = await learning.memoryPrompt();
  const adbPrompt = options.serverUrl
    ? "This evaluation uses the AndroidWorld FastAPI transport. Direct ADB access is unavailable; use only the registered Android GUI tools for device observation and actions."
    : options.serial
      ? `ADB target for this task: executable ${JSON.stringify((device as AdbDevice).adbPath)}, serial ${JSON.stringify(options.serial)}. Include -s ${JSON.stringify(options.serial)} in direct adb commands.`
      : `ADB executable for this task: ${JSON.stringify((device as AdbDevice).adbPath)}. No serial was specified; direct adb commands may use the single connected device.`;
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager,
    noExtensions: true,
    additionalSkillPaths: [
      ...(ledgerEnabled ? [resolve(cwd, "skills")] : []),
      learning.skillsDir,
    ],
    noPromptTemplates: true,
    noContextFiles: true,
    systemPrompt: systemPrompt(ledgerEnabled),
    appendSystemPrompt: [adbPrompt, ...(memoryPrompt ? [memoryPrompt] : [])],
    extensionFactories: [(pi) => registerAndroidTools(pi, androidTools, state, maxSteps)],
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
    let completedNormally = false;
    try {
      await session.prompt(`Task: ${task}\n\nThe attached image is the current phone screen.`, {
        images: [initialImage(await device.screenshot())],
      });
      for (let attempt = 1; attempt <= MULTIMODAL_RETRY_LIMIT && !state.finished && !state.stalled && !state.aborted; attempt++) {
        if (!latestMultimodalProviderError(session.messages)) break;
        await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
        await session.prompt(
          `The image provider rejected the previous screenshot. Continue the same task using this fresh current-screen capture (retry ${attempt}/${MULTIMODAL_RETRY_LIMIT}).`,
          { images: [initialImage(await device.screenshot())] },
        );
      }
      completedNormally = !latestMultimodalProviderError(session.messages);
    } catch (error) {
      if (!state.finished && !state.stalled && !state.aborted) throw error;
    }
    if (!ledgerEnabled && completedNormally && !state.stalled && !state.aborted) {
      state.answer = finalAssistantText(session.messages);
      state.finished = true;
    }
    if (!state.aborted && options.learning !== false && session.model) {
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

function registerAndroidTools(
  pi: ExtensionAPI,
  androidTools: ReturnType<typeof createAndroidTools>,
  state: AgentState,
  maxSteps: number,
): void {
  const names = androidTools.map((tool) => tool.name);
  androidTools.forEach((tool) => pi.registerTool(tool));

  pi.on("session_start", () => {
    pi.setActiveTools([...new Set([...pi.getActiveTools(), ...names])]);
  });

  pi.on("turn_start", (_event, context) => {
    if (abortIfStepLimitReached(state, maxSteps)) context.abort();
  });

  pi.on("message_end", ({ message }) => {
    recordThinkingActionStep(state, message);
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
