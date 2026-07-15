import type { ImageContent } from "@earendil-works/pi-ai";
import { type ToolDefinition, defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { AdbDevice, type ScreenSize } from "./adb.js";
import { resolveApp } from "./apps.js";

export interface AgentState {
  actions: number;
  answer?: string;
  finished: boolean;
}

export interface ToolOptions {
  settleMs?: number;
  maxActions?: number;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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
  device: AdbDevice,
  state: AgentState,
  options: ToolOptions = {},
): ToolDefinition[] {
  const settleMs = options.settleMs ?? 1_500;
  const maxActions = options.maxActions ?? 30;

  const observe = async (message: string) => ({
    content: [{
      type: "text" as const,
      text: `${message}\nVisible UI text:\n${(await device.visibleText()).join("\n") || "(none)"}`,
    }, image(await device.screenshot())],
    details: {},
  });

  const act = async (description: string, action: () => Promise<void>) => {
    if (state.actions >= maxActions) {
      throw new Error(`Action limit (${maxActions}) reached. Finish with the best verified result.`);
    }
    state.actions += 1;
    await action();
    await sleep(settleMs);
    return observe(`${description}. Screenshot after action:`);
  };

  return [
    defineTool({
      name: "observe",
      label: "Observe screen",
      description: "Capture the current Android screen without changing it.",
      parameters: Type.Object({}),
      execute: async () => observe("Current screen:"),
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
        return act(`Tapped (${x}, ${y})`, () => device.tap(px, py));
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
        return act(`Long-pressed (${x}, ${y})`, () => device.swipe(px, py, px, py, duration_ms ?? 800));
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
        return act("Swiped", () => device.swipe(startX, startY, endX, endY, duration_ms ?? 400));
      },
    }),
    defineTool({
      name: "type_text",
      label: "Type text",
      description: "Type into the currently focused input field. This does not tap or clear the field first.",
      parameters: Type.Object({ text: Type.String({ minLength: 1 }) }),
      execute: async (_id, { text }) => act(`Typed ${JSON.stringify(text)}`, () => device.typeText(text)),
    }),
    defineTool({
      name: "system_button",
      label: "System button",
      description: "Press an Android system button.",
      parameters: Type.Object({
        button: Type.Union([Type.Literal("back"), Type.Literal("home"), Type.Literal("enter")]),
      }),
      execute: async (_id, { button }) => {
        const codes = { back: "KEYCODE_BACK", home: "KEYCODE_HOME", enter: "KEYCODE_ENTER" };
        return act(`Pressed ${button}`, () => device.key(codes[button]));
      },
    }),
    defineTool({
      name: "open_app",
      label: "Open app",
      description: "Open an app by its known friendly name or Android package name.",
      parameters: Type.Object({ name: Type.String({ minLength: 1 }) }),
      execute: async (_id, { name }) =>
        act(`Opened ${name}`, () => device.openApp(resolveApp(name))),
    }),
    defineTool({
      name: "wait",
      label: "Wait",
      description: "Wait briefly for loading or an animation, then capture the screen.",
      parameters: Type.Object({ seconds: Type.Optional(Type.Number({ minimum: 1, maximum: 10 })) }),
      execute: async (_id, { seconds }) => {
        await sleep((seconds ?? 2) * 1000);
        return observe("Screen after waiting:");
      },
    }),
    defineTool({
      name: "answer",
      label: "Answer user",
      description: "Record the explicit answer to a question-based task. Call this before finish.",
      parameters: Type.Object({ text: Type.String({ minLength: 1 }) }),
      execute: async (_id, { text }) => {
        state.answer = text;
        return { content: [{ type: "text", text: `Answer recorded: ${text}` }], details: {} };
      },
    }),
    defineTool({
      name: "finish",
      label: "Finish task",
      description: "Mark the task complete only after verifying the requested result on screen.",
      parameters: Type.Object({ summary: Type.String({ minLength: 1 }) }),
      execute: async (_id, { summary }) => {
        state.finished = true;
        return { content: [{ type: "text", text: `Task marked complete: ${summary}` }], details: {} };
      },
    }),
  ] as ToolDefinition[];
}
