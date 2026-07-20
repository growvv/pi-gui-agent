import type { ImageContent } from "@earendil-works/pi-ai";
import { type ToolDefinition, defineTool } from "@earendil-works/pi-coding-agent";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Type } from "typebox";
import type { AndroidDevice, ScreenSize, UiElement } from "./adb.js";
import { resolveApp } from "./apps.js";

export interface AgentState {
  actions: number;
  steps?: number;
  answer?: string;
  finished: boolean;
  aborted?: boolean;
  abortReason?: string;
  lastUiFingerprint?: string;
  lastActionSignature?: string;
  unchangedActions?: number;
  repeatedActions?: number;
  progressWarnings?: number;
  stalled?: boolean;
  ledgerRequired?: boolean;
  ledgerValidated?: boolean;
  ledgerTaskCompleted?: boolean;
  ledgerOutput?: string;
  ledgerPath?: string;
  ledgerDigest?: string;
  ledgerSource?: string;
  lastUiElements?: UiElement[];
}

export interface ToolOptions {
  settleMs?: number;
  maxActions?: number;
  maxNoProgressActions?: number;
  ledgerRequired?: boolean;
  ledgerEnabled?: boolean;
  originalTask?: string;
  ledgerDir?: string;
  screenshotArchiveDir?: string;
  appMap?: Record<string, string>;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const LEDGER_KINDS = ["subtask", "subtask_for_validate", "complete_subtask", "complete"] as const;
const LEDGER_TOOL_NAMES = new Set([
  "update_ledger", "reflect_on_ledger", "validate_ledger", "answer", "finish",
]);
type LedgerKind = typeof LEDGER_KINDS[number];

interface LedgerRecordInput {
  kind: LedgerKind;
  id?: string;
  detail?: string;
}

const quoteLedgerValue = (value: string) => JSON.stringify(value);

function ledgerRecord(input: LedgerRecordInput): string {
  const required = (...values: Array<string | undefined>) => {
    if (values.some((value) => !value?.trim())) throw new Error(`${input.kind} is missing a required field.`);
    return values.map((value) => quoteLedgerValue(value!)).join(" ");
  };
  switch (input.kind) {
    case "subtask": return `subtask ${required(input.id, input.detail)}`;
    case "subtask_for_validate": return `subtask_for_validate ${required(input.id, input.detail)}`;
    case "complete_subtask": return `complete_subtask ${required(input.id)}`;
    case "complete": return `complete ${required(input.detail)}`;
  }
}

function ledgerRecordMatches(line: string, input: LedgerRecordInput): boolean {
  const row = records(line, input.kind)[0];
  if (!row) return false;
  if (input.kind === "complete") return true;
  return row[0] === input.id;
}

function records(source: string, name: string): string[][] {
  const lines = source.match(new RegExp(`^\\s*${name}\\s+.*$`, "gm")) ?? [];
  return lines.map((line) => [...line.matchAll(/"(?:\\.|[^"\\])*"|'[^']*'/g)]
    .map((match) => match[0]!.startsWith('"')
      ? JSON.parse(match[0]!) as string
      : match[0]!.slice(1, -1)));
}

export function inspectExecutionLedger(source: string, originalTask?: string): string[] {
  void originalTask;
  const issues: string[] = [];
  if (!/^#!\/.*\bbash\b/m.test(source)) issues.push("missing Bash shebang");
  const tasks = records(source, "task");
  const subtasks = records(source, "subtask");
  const validationSubtasks = records(source, "subtask_for_validate");
  const reflections = records(source, "reflection");
  const answers = records(source, "answer");
  const completedSubtasks = records(source, "complete_subtask");
  const completions = records(source, "complete");
  const validations = records(source, "validation");
  const finishes = records(source, "finish");
  const subtaskIds = subtasks.map((record) => record[0]).filter(Boolean);
  const validationIds = validationSubtasks.map((record) => record[0]).filter(Boolean);
  const allSubtaskIds = [...subtaskIds, ...validationIds];
  const knownIds = new Set(allSubtaskIds);
  const requireShape = (name: string, rows: string[][], count: number) => {
    if (rows.some((row) => row.length !== count || row.some((value) => !value.trim()))) {
      issues.push(`${name} records require exactly ${count} non-empty quoted arguments`);
    }
  };
  if (tasks.length !== 1) issues.push("must contain exactly one task \"...\" declaration");
  requireShape("task", tasks, 1);
  requireShape("subtask", subtasks, 2);
  requireShape("subtask_for_validate", validationSubtasks, 2);
  if (new Set(allSubtaskIds).size !== allSubtaskIds.length) issues.push("subtask IDs must be unique");
  requireShape("reflection", reflections, 3);
  requireShape("answer", answers, 1);
  requireShape("complete_subtask", completedSubtasks, 1);
  requireShape("complete", completions, 1);
  requireShape("validation", validations, 2);
  requireShape("finish", finishes, 2);
  if (completions.length > 1) issues.push("must contain at most one task completion record");
  if (answers.length > 1) issues.push("must contain at most one answer record");
  if (completedSubtasks.some(([id]) => id && !knownIds.has(id))) {
    issues.push("completed subtasks must reference a declared subtask ID");
  }
  return issues;
}

export function normalizedPoint(x: number, y: number, screen: ScreenSize): [number, number] {
  const clamp = (value: number) => Math.max(0, Math.min(1000, value));
  return [
    Math.round((clamp(x) / 1000) * (screen.width - 1)),
    Math.round((clamp(y) / 1000) * (screen.height - 1)),
  ];
}

function image(buffer: Buffer): ImageContent {
  return { type: "image", data: buffer.toString("base64"), mimeType: "image/png" };
}

export function createAndroidTools(
  device: AndroidDevice,
  state: AgentState,
  options: ToolOptions = {},
): ToolDefinition[] {
  const settleMs = options.settleMs ?? 1_500;
  const maxActions = options.maxActions ?? 30;
  const maxNoProgressActions = options.maxNoProgressActions ?? 4;
  const ledgerEnabled = options.ledgerEnabled ?? true;
  const ledgerDir = resolve(options.ledgerDir ?? ".pi/ledgers");
  state.ledgerRequired = ledgerEnabled && options.ledgerRequired;

  const commitLedger = async (source: string) => {
    if (!state.ledgerPath) throw new Error("Managed ledger path was not initialized.");
    await writeFile(state.ledgerPath, source, "utf8");
    state.ledgerDigest = createHash("sha256").update(source).digest("hex");
    state.ledgerSource = source;
    state.ledgerValidated = false;
    state.ledgerTaskCompleted = false;
  };

  const ensureLedger = async () => {
    if (!state.ledgerPath) {
      await mkdir(ledgerDir, { recursive: true });
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const taskHash = createHash("sha256").update(options.originalTask ?? "Unknown task").digest("hex").slice(0, 12);
      state.ledgerPath = `${ledgerDir}/${timestamp}-${taskHash}-${randomUUID()}.sh`;
      const functions = ["task", "subtask", "subtask_for_validate", "reflection", "answer", "complete_subtask", "complete", "validation", "finish"]
        .map((name) => `${name}() { :; }`).join("\n");
      await commitLedger(`#!/usr/bin/env bash\n${functions}\ntask ${quoteLedgerValue(options.originalTask ?? "Unknown task")}\n`);
    }
    return state.ledgerPath;
  };

  const readManagedLedger = async () => {
    const path = await ensureLedger();
    let source: string;
    try {
      source = await readFile(path, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT" || !state.ledgerSource) throw error;
      await writeFile(path, state.ledgerSource, "utf8");
      throw new Error("Managed ledger was deleted outside its tools; the canonical state was restored. Retry the managed tool call.");
    }
    const digest = createHash("sha256").update(source).digest("hex");
    if (state.ledgerDigest && digest !== state.ledgerDigest) {
      if (state.ledgerSource) await writeFile(path, state.ledgerSource, "utf8");
      throw new Error("Managed ledger was modified outside its tools; the canonical state was restored. Retry the managed tool call.");
    }
    return { path, source };
  };

  const observe = async (message: string, progressMessage?: string) => {
    const [uiElements, screenshot] = await Promise.all([device.uiElements(), device.screenshot()]);
    state.lastUiElements = uiElements;
    const visibleText = [...new Set(uiElements.flatMap((element) => [element.text, element.contentDesc])
      .filter((value): value is string => typeof value === "string" && value.trim().length > 0))];
    const elementList = uiElements.map(formatUiElement).join("\n");
    const fingerprint = createHash("sha256").update(screenshot).update("\0").update(visibleText.join("\n")).digest("hex");
    const observedAt = new Date().toISOString();
    let archivePath: string | undefined;
    let archiveError: string | undefined;
    if (options.screenshotArchiveDir) {
      try {
        await mkdir(options.screenshotArchiveDir, { recursive: true });
        archivePath = `${options.screenshotArchiveDir}/action-${String(state.actions).padStart(4, "0")}-${observedAt.replace(/[:.]/g, "-")}-${fingerprint.slice(0, 12)}.png`;
        await writeFile(archivePath, screenshot);
      } catch (error) {
        archivePath = undefined;
        archiveError = error instanceof Error ? error.message : String(error);
      }
    }
    return {
    content: [{
      type: "text" as const,
      text: `${message}${progressMessage ? `\n\n${progressMessage}` : ""}\nObserved at: ${observedAt}\nScreenshot fingerprint: ${fingerprint}\nScreenshot archive: ${archivePath ?? (archiveError ? `failed: ${archiveError}` : "disabled")}\nVisible UI text:\n${visibleText.join("\n") || "(none)"}\nUI elements:\n${elementList || "(none)"}`,
    }, image(screenshot)],
    details: { observedAt, fingerprint, archivePath, archiveError },
    fingerprint,
  }};

  const act = async (signature: string, description: string, action: () => Promise<void>) => {
    if (state.actions >= maxActions) {
      throw new Error(`Action limit (${maxActions}) reached. Finish with the best verified result.`);
    }
    const repeatedSignature = state.lastActionSignature === signature;
    state.lastActionSignature = signature;
    state.actions += 1;
    await action();
    await sleep(settleMs);
    const result = await observe(`${description}. Screenshot after action:`);
    const unchanged = result.fingerprint === state.lastUiFingerprint;
    state.unchangedActions = unchanged ? (state.unchangedActions ?? 0) + 1 : 0;
    state.repeatedActions = repeatedSignature && unchanged ? (state.repeatedActions ?? 0) + 1 : 0;
    state.lastUiFingerprint = result.fingerprint;
    let warning: string | undefined;
    if (state.repeatedActions === maxNoProgressActions || state.unchangedActions === maxNoProgressActions) {
      state.progressWarnings = (state.progressWarnings ?? 0) + 1;
      warning = "PROGRESS GUARD: repeated actions have not changed the visible UI. Do not repeat this action. Observe the current state, diagnose why it had no effect, and choose a different strategy (a deterministic adb/CLI check may help).";
    }
    if ((state.unchangedActions ?? 0) >= maxNoProgressActions * 2) {
      state.stalled = true;
      throw new Error(`Progress guard stopped the run after ${state.unchangedActions} device actions produced no visible change.`);
    }
    if (!warning) return { content: result.content, details: result.details };
    const first = result.content[0];
    if (first?.type === "text") first.text = `${warning}\n\n${first.text}`;
    return {
      content: result.content,
      details: {
        progressWarning: true,
        observedAt: result.details.observedAt,
        fingerprint: result.details.fingerprint,
        archivePath: result.details.archivePath,
        archiveError: result.details.archiveError,
      },
    };
  };

  const tools = [
    defineTool({
      name: "screenshot",
      label: "Screenshot",
      description: "Capture the current screen and visible UI elements without performing an action. The result includes `Screenshot archive: <path>`, which is the output path of the saved PNG when screenshot archiving is enabled.",
      parameters: Type.Object({}),
      execute: async () => {
        const result = await observe("Current screen:");
        return { content: result.content, details: result.details };
      },
    }),
    defineTool({
      name: "click",
      label: "Click UI element",
      description: "Click a clickable UI element by the index shown in the latest screenshot or action result. Take a new screenshot if the UI has changed.",
      parameters: Type.Object({ index: Type.Integer({ minimum: 0 }) }),
      execute: async (_id, { index }) => {
        const observed = state.lastUiElements?.find((element) => element.index === index);
        if (!observed) throw new Error(`UI element [${index}] was not present in the latest observation. Take a screenshot and use a listed index.`);
        const current = (await device.uiElements()).find((element) => element.index === index);
        if (!current || uiElementIdentity(current) !== uiElementIdentity(observed)) {
          throw new Error(`UI element [${index}] changed since the latest observation. Take a new screenshot before clicking.`);
        }
        return act(`click:${uiElementIdentity(current)}`, `Clicked UI element [${index}]`, () => device.clickElement(current));
      },
    }),
    defineTool({
      name: "tap",
      label: "Tap",
      description: "Tap a visible point. Coordinates range from 0 to 1000 relative to the screenshot.",
      parameters: Type.Object({
        x: Type.Number({ minimum: 0, maximum: 1000 }),
        y: Type.Number({ minimum: 0, maximum: 1000 }),
      }),
      execute: async (_id, { x, y }) => {
        const [px, py] = normalizedPoint(x, y, await device.screenSize());
        return act(`tap:${px}:${py}`, `Tapped (${x}, ${y})`, () => device.tap(px, py));
      },
    }),
    defineTool({
      name: "long_press",
      label: "Long press",
      description: "Long-press a visible point using normalized 0..1000 coordinates.",
      parameters: Type.Object({
        x: Type.Number({ minimum: 0, maximum: 1000 }),
        y: Type.Number({ minimum: 0, maximum: 1000 }),
        duration_ms: Type.Optional(Type.Number({ minimum: 300, maximum: 3000 })),
      }),
      execute: async (_id, { x, y, duration_ms }) => {
        const [px, py] = normalizedPoint(x, y, await device.screenSize());
        return act(`long_press:${px}:${py}`, `Long-pressed (${x}, ${y})`, () => device.swipe(px, py, px, py, duration_ms ?? 800));
      },
    }),
    defineTool({
      name: "swipe",
      label: "Swipe",
      description: "Swipe between two normalized 0..1000 coordinates.",
      parameters: Type.Object({
        x1: Type.Number({ minimum: 0, maximum: 1000 }),
        y1: Type.Number({ minimum: 0, maximum: 1000 }),
        x2: Type.Number({ minimum: 0, maximum: 1000 }),
        y2: Type.Number({ minimum: 0, maximum: 1000 }),
        duration_ms: Type.Optional(Type.Number({ minimum: 100, maximum: 3000 })),
      }),
      execute: async (_id, { x1, y1, x2, y2, duration_ms }) => {
        const screen = await device.screenSize();
        const [startX, startY] = normalizedPoint(x1, y1, screen);
        const [endX, endY] = normalizedPoint(x2, y2, screen);
        return act(`swipe:${startX}:${startY}:${endX}:${endY}`, "Swiped", () => device.swipe(startX, startY, endX, endY, duration_ms ?? 400));
      },
    }),
    defineTool({
      name: "type_text",
      label: "Type text",
      description: "Type into the currently focused editable field, including Unicode via ADB Keyboard. Fails if no editable field is focused; this does not tap or clear the field first.",
      parameters: Type.Object({ text: Type.String({ minLength: 1 }) }),
      execute: async (_id, { text }) => act(`type_text:${text}`, `Typed ${JSON.stringify(text)}`, () => device.typeText(text)),
    }),
    defineTool({
      name: "back",
      label: "Back",
      description: "Press the Android Back button.",
      parameters: Type.Object({}),
      execute: async () => act("back", "Pressed Back", () => device.key("KEYCODE_BACK")),
    }),
    defineTool({
      name: "open_app",
      label: "Open app",
      description: "Open an app by its known friendly name or Android package name.",
      parameters: Type.Object({ name: Type.String({ minLength: 1 }) }),
      execute: async (_id, { name }) =>
        act(`open_app:${name.toLowerCase()}`, `Opened ${name}`, () => device.openApp(resolveApp(name, options.appMap))),
    }),
    defineTool({
      name: "update_ledger",
      label: "Update execution ledger",
      description: "Create subtasks, mark subtasks complete, or record overall task completion. Batch related execution records in one call.",
      parameters: Type.Object({
        operation: Type.Optional(Type.Union([Type.Literal("append"), Type.Literal("replace"), Type.Literal("remove")])),
        records: Type.Array(Type.Object({
          kind: Type.Union(LEDGER_KINDS.map((kind) => Type.Literal(kind))),
          id: Type.Optional(Type.String({ minLength: 1 })),
          detail: Type.Optional(Type.String({ minLength: 1 })),
        }), { minItems: 1, maxItems: 100 }),
      }),
      execute: async (_id, args) => {
        const patch = args as { operation?: "append" | "replace" | "remove"; records: LedgerRecordInput[] };
        const { path, source } = await readManagedLedger();
        const operation = patch.operation ?? "append";
        const lines = source.trimEnd().split("\n");
        if (operation === "append") {
          if (records(source, "complete").length > 0 && patch.records.some(({ kind }) => kind === "complete")) {
            throw new Error("Task completion is already recorded; replace the existing complete record instead of adding another one.");
          }
          lines.push(...patch.records.map(ledgerRecord));
        } else {
          for (const input of patch.records) {
            let index = -1;
            for (let i = lines.length - 1; i >= 0; i--) {
              if (ledgerRecordMatches(lines[i]!, input)) {
                index = i;
                break;
              }
            }
            if (index < 0) throw new Error(`No matching ${input.kind} record was found.`);
            if (operation === "replace") lines[index] = ledgerRecord(input);
            else lines.splice(index, 1);
          }
        }
        const updated = `${lines.join("\n")}\n`;
        await commitLedger(updated);
        const declared = records(updated, "subtask").length + records(updated, "subtask_for_validate").length;
        const completed = new Set(records(updated, "complete_subtask").map(([id]) => id)).size;
        return {
          content: [{ type: "text", text: `LEDGER UPDATED\nTracked subtasks: ${completed}/${declared} marked complete.` }],
          details: { path, operation, records: patch.records.length, declared, completed },
        };
      },
    }),
    defineTool({
      name: "reflect_on_ledger",
      label: "Reflect on execution ledger",
      description: "Record a reflection when you judge it useful, such as after failure, repetition, uncertainty, or a change of plan. You decide when to call it and what to do next.",
      parameters: Type.Object({
        current_subtask_id: Type.Optional(Type.String({ minLength: 1, description: "Current subtask ID, or omit for a task-level reflection" })),
        reason: Type.String({ minLength: 1, description: "What happened and why reflection is useful now" }),
        next_step: Type.String({ minLength: 1, description: "The concrete next action or revised approach" }),
      }),
      execute: async (_id, { current_subtask_id, reason, next_step }) => {
        const { path, source } = await readManagedLedger();
        const subtask = current_subtask_id ?? "task";
        const reflection = `reflection ${quoteLedgerValue(subtask)} ${quoteLedgerValue(reason)} ${quoteLedgerValue(next_step)}`;
        const updated = `${source.trimEnd()}\n${reflection}\n`;
        await commitLedger(updated);
        return {
          content: [{ type: "text", text: `LEDGER REFLECTION RECORDED (${path})\nReason: ${reason}\nNext step: ${next_step}` }],
          details: { path, subtask, reason, nextStep: next_step },
        };
      },
    }),
    defineTool({
      name: "validate_ledger",
      label: "Validate execution ledger",
      description: "Review the original task, tracked subtasks, execution history, and current visible UI. Semantically judge whether the task and its necessary subtasks are complete; this records your judgment instead of applying a strict schema gate.",
      parameters: Type.Object({
        task_completed: Type.Boolean({ description: "True only when you judge the requested task and all necessary subtasks complete" }),
        summary: Type.String({ minLength: 1, description: "Why the task is complete, or what remains to be done" }),
        incomplete_subtasks: Type.Optional(Type.Array(Type.String({ minLength: 1 }), { maxItems: 20, description: "IDs or descriptions of work still incomplete" })),
      }),
      execute: async (_id, { task_completed, summary, incomplete_subtasks }) => {
        const { source } = await readManagedLedger();
        const baseSource = `${source.trimEnd()}\n`;
        const declared = records(baseSource, "subtask").map(([id]) => id).filter(Boolean);
        const validationIds = records(baseSource, "subtask_for_validate").map(([id]) => id).filter(Boolean);
        const completed = new Set(records(baseSource, "complete_subtask").map(([id]) => id).filter(Boolean));
        const openSubtasks = [...declared, ...validationIds].filter((id) => !completed.has(id));
        if (task_completed && validationIds.some((id) => !completed.has(id))) {
          throw new Error(`Cannot validate complete: validation subtask(s) still open: ${validationIds.filter((id) => !completed.has(id)).join(", ")}`);
        }
        const remaining = (incomplete_subtasks as string[] | undefined) ?? [];
        const output = remaining.length ? `${summary} Remaining: ${remaining.join(", ")}` : summary;
        const status = task_completed ? "complete" : "incomplete";
        await commitLedger(`${baseSource}validation ${quoteLedgerValue(status)} ${quoteLedgerValue(output)}\n`);
        state.ledgerValidated = true;
        state.ledgerTaskCompleted = task_completed;
        state.ledgerOutput = output;
        const bookkeeping = `Tracked subtasks: ${completed.size}/${declared.length} marked complete${openSubtasks.length ? `; open: ${openSubtasks.join(", ")}` : ""}.`;
        const next = task_completed
          ? state.ledgerRequired
            ? "Call finish next."
            : "Semantic validation is complete; stop only after the final result is verified."
          : state.ledgerRequired
            ? "Continue the GUI task, update the ledger when work completes, then validate again before finish."
            : "Continue the GUI task, update the ledger when work completes, then validate again before stopping.";
        return {
          content: [{ type: "text", text: `LEDGER VALIDATION RECORDED: ${status.toUpperCase()}\n${output}\n${bookkeeping}\n${next}` }],
          details: { taskCompleted: task_completed, summary: output, declared, completed: [...completed], openSubtasks },
        };
      },
    }),
    defineTool({
      name: "answer",
      label: "Answer user",
      description: "Record the explicit answer to a question-based task. Call this before finish.",
      parameters: Type.Object({ text: Type.String({ minLength: 1 }) }),
      execute: async (_id, { text }) => {
        const { path, source } = await readManagedLedger();
        const lines = source.trimEnd().split("\n").filter((line) => !/^\s*answer\s+['\"]/.test(line));
        const answerRecord = `answer ${quoteLedgerValue(text)}`;
        const completeIndex = lines.findIndex((line) => /^\s*complete\s+['\"]/.test(line));
        if (completeIndex >= 0) lines.splice(completeIndex, 0, answerRecord);
        else lines.push(answerRecord);
        const updated = `${lines.join("\n")}\n`;
        await commitLedger(updated);
        state.answer = text;
        return {
          content: [{ type: "text", text: `Answer recorded in ledger: ${text}` }],
          details: { path },
        };
      },
    }),
    defineTool({
      name: "finish",
      label: "Finish task",
      description: "Finish after validate_ledger has recorded your latest semantic review. Validation is a sequencing check, not a strict completion gate.",
      parameters: Type.Object({ summary: Type.String({ minLength: 1 }) }),
      execute: async (_id, { summary }) => {
        const { source } = await readManagedLedger();
        if (state.ledgerRequired && !state.ledgerValidated) {
          throw new Error("Call validate_ledger after the latest ledger update and before finish.");
        }
        if (state.ledgerRequired && state.ledgerTaskCompleted !== true) {
          throw new Error("Cannot finish after an incomplete ledger validation; complete the task and validate again.");
        }
        const validationIds = records(source, "subtask_for_validate").map(([id]) => id).filter(Boolean);
        const completed = new Set(records(source, "complete_subtask").map(([id]) => id).filter(Boolean));
        const openValidation = validationIds.filter((id) => !completed.has(id));
        if (state.ledgerRequired && openValidation.length > 0) {
          throw new Error(`Complete subtask_for_validate before finish: ${openValidation.join(", ")}`);
        }
        const taskCompleted = state.ledgerTaskCompleted;
        await commitLedger(`${source.trimEnd()}\nfinish "passed" ${quoteLedgerValue(summary)}\n`);
        state.ledgerValidated = true;
        state.ledgerTaskCompleted = taskCompleted;
        state.finished = true;
        return { content: [{ type: "text", text: `Task marked complete: ${summary}` }], details: {} };
      },
    }),
  ] as ToolDefinition[];
  return ledgerEnabled
    ? tools
    : tools.filter((tool) => !LEDGER_TOOL_NAMES.has(tool.name));
}

function uiElementIdentity(element: UiElement): string {
  return [element.resourceId, element.contentDesc, element.text, element.className, element.bounds].join("\0");
}

function formatUiElement(element: UiElement): string {
  const attributes = [
    element.text ? `text=${JSON.stringify(element.text)}` : undefined,
    element.contentDesc ? `desc=${JSON.stringify(element.contentDesc)}` : undefined,
    element.resourceId ? `id=${JSON.stringify(element.resourceId)}` : undefined,
    `clickable=${element.clickable}`,
    `enabled=${element.enabled}`,
    `bounds=${element.bounds}`,
  ].filter(Boolean).join(" ");
  return `[${element.index}] ${element.className ?? "Element"} ${attributes}`;
}
