#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { AdbDevice } from "./adb.js";
import { createAndroidTools, type AgentState } from "./tools.js";

const GUI_TOOLS = new Set([
  "screenshot", "tap", "long_press", "swipe", "type_text", "open_app", "back",
]);

const schemas: Record<string, z.ZodRawShape> = {
  screenshot: {},
  tap: { x: z.number().min(0).max(1000), y: z.number().min(0).max(1000) },
  long_press: {
    x: z.number().min(0).max(1000), y: z.number().min(0).max(1000),
    duration_ms: z.number().min(300).max(3000).optional(),
  },
  swipe: {
    x1: z.number().min(0).max(1000), y1: z.number().min(0).max(1000),
    x2: z.number().min(0).max(1000), y2: z.number().min(0).max(1000),
    duration_ms: z.number().min(100).max(3000).optional(),
  },
  type_text: { text: z.string().min(1) },
  open_app: { name: z.string().min(1) },
  back: {},
};

function option(name: string, fallback?: string): string | undefined {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

const device = new AdbDevice({
  adbPath: option("adb", process.env.PI_ADB_PATH),
  serial: option("serial", process.env.ANDROID_SERIAL),
  onWarning: (message) => console.error(message),
});
const state: AgentState = { actions: 0, finished: false };
const tools = createAndroidTools(device, state, {
  settleMs: Number(option("settle-ms", "1500")),
  maxActions: Number(option("max-actions", "30")),
  screenshotArchiveDir: option("screenshot-dir"),
});
const server = new McpServer({ name: "android-gui", version: "1.0.0" });

for (const tool of tools) {
  if (!GUI_TOOLS.has(tool.name)) continue;
  server.registerTool(
    tool.name,
    { description: tool.description, inputSchema: schemas[tool.name] ?? {} },
    async (args) => {
      try {
        const result = await (tool.execute as any)(`mcp-${Date.now()}`, args);
        return { content: result.content };
      } catch (error) {
        return {
          content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
          isError: true,
        };
      }
    },
  );
}

const shutdown = async () => {
  await device.restoreInputMethod();
  process.exit(0);
};
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
await server.connect(new StdioServerTransport());
