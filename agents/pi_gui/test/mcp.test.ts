import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const temporaryDirectories: string[] = [];

async function connect(toolset?: "gui" | "ledger", ledgerDir?: string) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [
      join(root, "node_modules", "tsx", "dist", "cli.mjs"),
      join(root, "src", "mcp.ts"),
      "--settle-ms", "0",
      "--task", "Test MCP tool isolation",
      ...(toolset ? ["--toolset", toolset] : []),
      ...(ledgerDir ? ["--ledger-dir", ledgerDir] : []),
    ],
  });
  const client = new Client({ name: "mcp-test", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((path) =>
    rm(path, { recursive: true, force: true })));
});

describe("MCP toolsets", () => {
  it("exposes only GUI tools by default", async () => {
    const client = await connect();
    try {
      const listed = await client.listTools();
      expect(listed.tools.map(({ name }) => name).sort()).toEqual([
        "back", "click", "long_press", "open_app", "screenshot", "swipe", "tap", "type_text",
      ]);
    } finally {
      await client.close();
    }
  }, 15_000);

  it("exposes and executes only ledger tools in ledger mode", async () => {
    const ledgerDir = await mkdtemp(join(tmpdir(), "pi-gui-mcp-ledger-"));
    temporaryDirectories.push(ledgerDir);
    const client = await connect("ledger", ledgerDir);
    try {
      const listed = await client.listTools();
      expect(listed.tools.map(({ name }) => name).sort()).toEqual([
        "answer", "finish", "reflect_on_ledger", "update_ledger", "validate_ledger",
      ]);
      const result = await client.callTool({
        name: "update_ledger",
        arguments: {
          records: [{ kind: "subtask", id: "inspect", detail: "Inspect the screen" }],
        },
      });
      expect(result.isError).not.toBe(true);
      expect(result.content[0]).toMatchObject({ type: "text" });
      const reflection = await client.callTool({
        name: "reflect_on_ledger",
        arguments: {
          current_subtask_id: "inspect",
          reason: "The expected control is not visible",
          next_step: "Capture a fresh screenshot",
        },
      });
      expect(reflection.isError).not.toBe(true);
      const validation = await client.callTool({
        name: "validate_ledger",
        arguments: {
          task_completed: false,
          summary: "The inspection subtask is still open",
          incomplete_subtasks: ["inspect"],
        },
      });
      expect(validation.isError).not.toBe(true);
      const answer = await client.callTool({
        name: "answer",
        arguments: { text: "The screen is ready" },
      });
      expect(answer.isError).not.toBe(true);
      const rejectedFinish = await client.callTool({
        name: "finish",
        arguments: { summary: "The validated screen is ready" },
      });
      expect(rejectedFinish.isError).toBe(true);

      const completeValidation = await client.callTool({
        name: "validate_ledger",
        arguments: {
          task_completed: true,
          summary: "The screen is ready",
          incomplete_subtasks: [],
        },
      });
      expect(completeValidation.isError).not.toBe(true);
      const finish = await client.callTool({
        name: "finish",
        arguments: { summary: "The validated screen is ready" },
      });
      expect(finish.isError).not.toBe(true);
    } finally {
      await client.close();
    }
  }, 15_000);
});
