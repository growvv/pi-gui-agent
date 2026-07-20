import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { AdbDevice } from "../src/adb.js";
import { shouldAbortAfterToolResult } from "../src/agent.js";
import { resolveApp } from "../src/apps.js";
import { createAndroidTools, inspectExecutionLedger, normalizedPoint, type AgentState } from "../src/tools.js";
import { LearningStore, summarizeTrajectory } from "../src/learning.js";

const temporaryDirectories: string[] = [];

describe("Agent completion", () => {
  it("stops the model loop after finish or a progress stall", () => {
    expect(shouldAbortAfterToolResult({ finished: true })).toBe(true);
    expect(shouldAbortAfterToolResult({ finished: false, stalled: true })).toBe(true);
    expect(shouldAbortAfterToolResult({ finished: false, stalled: false })).toBe(false);
  });
});

describe("AdbDevice", () => {
  it("uses the screenshot dimensions in the current display orientation", async () => {
    const image = Buffer.alloc(24);
    Buffer.from("89504e470d0a1a0a", "hex").copy(image);
    image.writeUInt32BE(2400, 16);
    image.writeUInt32BE(1080, 20);
    const device = new AdbDevice();
    device.screenshot = async () => image;

    await expect(device.screenSize()).resolves.toEqual({ width: 2400, height: 1080 });
  });

  it("uses ADB Keyboard base64 input and restores the previous IME", async () => {
    const device = new AdbDevice();
    const calls: string[][] = [];
    device.shell = async (...received) => {
      calls.push(received);
      if (received[0] === "uiautomator") return "UI hierchary dumped";
      if (received[0] === "cat") return '<hierarchy><node class="android.widget.EditText" editable="true" focused="true" bounds="[0,0][100,100]" /></hierarchy>';
      if (received[0] === "pm") return "package:/data/app/adbkeyboard.apk\n";
      if (received[0] === "settings") return "com.example/.Keyboard\n";
      if (received[0] === "ime" && received[1] === "list") return "com.example/.Keyboard\n";
      return "";
    };

    await device.typeText("你好，world!\n第二行");
    await device.restoreInputMethod();

    expect(calls).toContainEqual([
      "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg",
      Buffer.from("你好，world!\n第二行").toString("base64"),
    ]);
    expect(calls).toContainEqual(["ime", "set", "com.example/.Keyboard"]);
    expect(calls).toContainEqual(["ime", "disable", "com.android.adbkeyboard/.AdbIME"]);
  });

  it("rejects text input when no editable field is focused", async () => {
    const device = new AdbDevice();
    const calls: string[][] = [];
    device.shell = async (...received) => {
      calls.push(received);
      if (received[0] === "cat") return '<hierarchy><node class="android.widget.EditText" editable="true" focused="false" bounds="[0,0][100,100]" /></hierarchy>';
      return "";
    };

    await expect(device.typeText("Schönberg, Liechtenstein")).rejects.toThrow("No editable text field is focused");
    expect(calls.some((call) => call[0] === "am" && call[1] === "broadcast")).toBe(false);
  });

  it("warns once and falls back to input text when ADB Keyboard is unavailable", async () => {
    const warnings: string[] = [];
    const device = new AdbDevice({ onWarning: (message) => warnings.push(message) });
    const calls: string[][] = [];
    device.shell = async (...received) => {
      calls.push(received);
      if (received[0] === "uiautomator") return "UI hierchary dumped";
      if (received[0] === "cat") return '<hierarchy><node class="android.widget.EditText" editable="true" focused="true" bounds="[0,0][100,100]" /></hierarchy>';
      return "";
    };

    await device.typeText("it's & safe; $(now)");
    await device.typeText("你好");

    expect(calls).toContainEqual(["input", "text", "'it'\\''s%s&%ssafe;%s$(now)'"]);
    expect(calls).toContainEqual(["input", "text", "'你好'"]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("may not preserve Unicode");
  });
});

describe("screenshot archiving", () => {
  it("still returns an observation when the archive cannot be written", async () => {
    const root = await mkdtemp(join(tmpdir(), "pi-gui-archive-"));
    temporaryDirectories.push(root);
    const blocked = join(root, "blocked");
    await writeFile(blocked, "not a directory");
    const call = toolCaller(createAndroidTools(
      staticDevice(), { actions: 0, finished: false },
      { settleMs: 0, screenshotArchiveDir: join(blocked, "screenshots") },
    ));

    const result = await call("tap", { x: 500, y: 500 });

    expect(result).toContain("Screenshot fingerprint:");
    expect(result).toContain("Screenshot archive: failed:");
  });
});

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

describe("normalizedPoint", () => {
  it("maps the model coordinate space to device pixels", () => {
    expect(normalizedPoint(0, 0, { width: 1080, height: 2400 })).toEqual([0, 0]);
    expect(normalizedPoint(500, 500, { width: 1080, height: 2400 })).toEqual([540, 1200]);
    expect(normalizedPoint(1000, 1000, { width: 1080, height: 2400 })).toEqual([1079, 2399]);
  });

  it("clamps out-of-range coordinates", () => {
    expect(normalizedPoint(-10, 1200, { width: 100, height: 200 })).toEqual([0, 199]);
  });
});

describe("Android GUI tools", () => {
  it("exposes the compact GUI tool set", () => {
    const names = createAndroidTools(staticDevice(), { actions: 0, finished: false }, { settleMs: 0 })
      .map((tool) => tool.name);

    expect(names).toEqual([
      "screenshot", "tap", "long_press", "swipe", "type_text", "back", "open_app",
      "update_ledger", "reflect_on_ledger", "validate_ledger", "answer", "finish",
    ]);
    expect(names).not.toContain("search_tools");
  });

  it("captures the current UI without consuming an action", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const call = toolCaller(createAndroidTools(staticDevice(), state, { settleMs: 0 }));

    const result = await call("screenshot", {});

    expect(result).toContain("Current screen:");
    expect(result).toContain("Visible UI text:");
    expect(state.actions).toBe(0);
  });
});

describe("progress guard", () => {
  it("warns when repeated actions leave the UI unchanged", async () => {
    const device = staticDevice();
    const state: AgentState = { actions: 0, finished: false };
    const tap = createAndroidTools(device, state, { settleMs: 0, maxNoProgressActions: 2 })
      .find((tool) => tool.name === "tap")!;

    await tap.execute("1", { x: 500, y: 500 }, undefined, undefined, {} as never);
    await tap.execute("2", { x: 500, y: 500 }, undefined, undefined, {} as never);
    const result = await tap.execute("3", { x: 500, y: 500 }, undefined, undefined, {} as never);

    expect(result.content[0]).toMatchObject({ type: "text" });
    expect(result.content[0].type === "text" && result.content[0].text).toContain("PROGRESS GUARD");
    expect(state.progressWarnings).toBe(1);
  });

  it("marks the run stalled after the hard no-progress threshold", async () => {
    const device = staticDevice();
    const state: AgentState = { actions: 0, finished: false };
    const tap = createAndroidTools(device, state, { settleMs: 0, maxNoProgressActions: 1 })
      .find((tool) => tool.name === "tap")!;

    await tap.execute("1", { x: 500, y: 500 }, undefined, undefined, {} as never);
    await tap.execute("2", { x: 500, y: 500 }, undefined, undefined, {} as never);
    await expect(tap.execute("3", { x: 500, y: 500 }, undefined, undefined, {} as never))
      .rejects.toThrow("Progress guard stopped");
    expect(state.stalled).toBe(true);
  });
});

describe("execution ledger", () => {
  it("writes the ledger into the configured result directory", async () => {
    const root = await mkdtemp(join(tmpdir(), "pi-gui-ledger-result-"));
    temporaryDirectories.push(root);
    const ledgerDir = join(root, "ledgers");
    const state: AgentState = { actions: 0, finished: false };
    const call = toolCaller(createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      originalTask: "Collect this ledger",
      ledgerDir,
    }));

    await call("update_ledger", { records: [{ kind: "subtask", id: "collect", detail: "Create a collected ledger" }] });

    expect(state.ledgerPath?.startsWith(`${ledgerDir}/`)).toBe(true);
    expect(await readFile(state.ledgerPath!, "utf8")).toContain('task "Collect this ledger"');
  });

  it("round-trips multiline and escaped original task text", async () => {
    const originalTask = 'Create note "Trip" with body:\nPack \\ charger';
    const source = `#!/usr/bin/env bash\ntask ${JSON.stringify(originalTask)}\n`;
    expect(inspectExecutionLedger(source, originalTask)).not.toContain(
      "task declaration does not exactly match the original task",
    );
  });

  it("tracks a lightweight lifecycle and requires validation before finish", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const tools = createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "Add a recipe in Broccoli",
    });
    const call = toolCaller(tools);

    await call("update_ledger", { records: [{ kind: "subtask", id: "recipe-1", detail: "Enter and save the recipe title" }] });
    temporaryDirectories.push(state.ledgerPath!);
    await expect(call("finish", { summary: "done" })).rejects.toThrow("Call validate_ledger");
    await call("update_ledger", { records: [
      { kind: "complete_subtask", id: "recipe-1" },
      { kind: "complete", detail: "Recipe was created and visibly verified" },
    ] });
    expect(await call("validate_ledger", {
      task_completed: true,
      summary: "The saved recipe is visible with the requested title",
      incomplete_subtasks: [],
    })).toContain("COMPLETE");
    await expect(call("finish", { summary: "done" })).resolves.toContain("Task marked complete");
    const finishedLedger = await readFile(state.ledgerPath!, "utf8");
    expect(finishedLedger).toContain('complete_subtask "recipe-1"');
    expect(finishedLedger).toContain('validation "complete"');
    expect(finishedLedger).toContain('finish "passed" "done"');
    expect(state.finished).toBe(true);
    expect(state.ledgerTaskCompleted).toBe(true);
  });

  it("records an incomplete semantic review and blocks finish", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const call = toolCaller(createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "Do two steps",
    }));
    await call("update_ledger", { records: [
      { kind: "subtask", id: "step-1", detail: "Do the first step" },
      { kind: "subtask", id: "step-2", detail: "Do the second step" },
      { kind: "complete_subtask", id: "step-1" },
    ] });
    temporaryDirectories.push(state.ledgerPath!);

    const validation = await call("validate_ledger", {
      task_completed: false,
      summary: "The second step remains",
      incomplete_subtasks: ["step-2"],
    });
    expect(validation).toContain("INCOMPLETE");
    expect(validation).toContain("open: step-2");
    await expect(call("finish", { summary: "Stopping after semantic review" }))
      .rejects.toThrow("incomplete ledger validation");
    expect(state.ledgerTaskCompleted).toBe(false);
    expect(state.finished).toBe(false);
  });

  it("requires a fresh semantic review after the ledger changes", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const call = toolCaller(createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "Complete a changing plan",
    }));
    await call("update_ledger", { records: [{ kind: "subtask", id: "step-1", detail: "First step" }] });
    temporaryDirectories.push(state.ledgerPath!);
    await call("validate_ledger", { task_completed: false, summary: "First step is open" });
    await call("update_ledger", { records: [{ kind: "complete_subtask", id: "step-1" }] });

    await expect(call("finish", { summary: "done" })).rejects.toThrow("Call validate_ledger");
    await call("validate_ledger", { task_completed: true, summary: "The tracked step is complete" });
    await expect(call("finish", { summary: "done" })).resolves.toContain("Task marked complete");
  });

  it("rejects and restores ledger modifications made outside the managed tools", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const tools = createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "Inspect Wi-Fi",
    });
    const call = toolCaller(tools);

    await call("update_ledger", { records: [{ kind: "subtask", id: "wifi", detail: "Read visible state" }] });
    temporaryDirectories.push(state.ledgerPath!);
    const source = await readFile(state.ledgerPath!, "utf8");
    await writeFile(state.ledgerPath!, `${source}settings get global wifi_on\n`, "utf8");
    const review = { task_completed: false, summary: "Still checking Wi-Fi" };
    await expect(call("validate_ledger", review)).rejects.toThrow("canonical state was restored");
    expect(await readFile(state.ledgerPath!, "utf8")).toBe(source);
    await expect(call("validate_ledger", review)).resolves.toContain("INCOMPLETE");
  });

  it("restores a managed ledger deleted outside its tools", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const call = toolCaller(createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "Turn wifi off",
    }));
    await call("update_ledger", { records: [{ kind: "subtask", id: "wifi", detail: "Turn wifi off" }] });
    const source = await readFile(state.ledgerPath!, "utf8");
    await rm(state.ledgerPath!);
    await expect(call("validate_ledger", { task_completed: false, summary: "Not done" }))
      .rejects.toThrow("canonical state was restored");
    expect(await readFile(state.ledgerPath!, "utf8")).toBe(source);
  });

  it("records a reflection supplied by the GUI agent", async () => {
    const state: AgentState = { actions: 2, finished: false, repeatedActions: 1 };
    const call = toolCaller(createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "Do both steps",
    }));
    await call("update_ledger", { records: [
      { kind: "subtask", id: "step-1", detail: "Do the first step" },
      { kind: "subtask", id: "step-2", detail: "Do the second step" },
    ] });

    const result = await call("reflect_on_ledger", {
      current_subtask_id: "step-2",
      reason: "The last two taps did not change the screen",
      next_step: "Take a screenshot and choose a different control",
    });
    expect(result).toContain("The last two taps did not change the screen");
    expect(result).toContain("Take a screenshot and choose a different control");
    const source = await readFile(state.ledgerPath!, "utf8");
    expect(source).toContain('reflection "step-2"');
    temporaryDirectories.push(state.ledgerPath!);
  });

  it("records and replaces the explicit answer in the managed ledger", async () => {
    const state: AgentState = { actions: 0, finished: false };
    const call = toolCaller(createAndroidTools(staticDevice(), state, {
      settleMs: 0,
      ledgerRequired: true,
      originalTask: "What is the visible result?",
    }));
    await call("update_ledger", { records: [
      { kind: "subtask", id: "answer-1", detail: "Read the visible result" },
      { kind: "complete_subtask", id: "answer-1" },
    ] });
    await call("answer", { text: "forty two" });
    await call("answer", { text: "42" });
    await call("update_ledger", { records: [{ kind: "complete", detail: "Visible answer recorded" }] });
    expect(await call("validate_ledger", { task_completed: true, summary: "Answer 42 is visible" })).toContain("COMPLETE");

    const source = await readFile(state.ledgerPath!, "utf8");
    expect(source).not.toContain('answer "forty two"');
    expect(source).toContain('answer "42"');
    expect(source.indexOf('answer "42"')).toBeLessThan(source.indexOf('complete "Visible answer recorded"'));
    temporaryDirectories.push(state.ledgerPath!);
  });

  it("accepts a structurally complete execution ledger", async () => {
    const source = `#!/usr/bin/env bash
task "Do task"
subtask "step-1" "Do step"
complete_subtask "step-1"
complete "Done"
`;
    expect(inspectExecutionLedger(source, "Do task")).toEqual([]);
  });

  it("reports bookkeeping problems without requiring task evidence", () => {
    const source = `#!/usr/bin/env bash
task "Do task"
subtask "step-1" "Do step"
complete_subtask "missing-step"
`;
    const issues = inspectExecutionLedger(source, "Do task");
    expect(issues).toEqual(["completed subtasks must reference a declared subtask ID"]);
  });
});

describe("resolveApp", () => {
  it("resolves Android World names", () => {
    expect(resolveApp("Simple Calendar Pro")).toBe("com.simplemobiletools.calendar.pro");
    expect(resolveApp("Markor")).toBe("net.gsantner.markor");
  });

  it("accepts a package name", () => {
    expect(resolveApp("com.example.app")).toBe("com.example.app");
  });

  it("accepts benchmark-specific app mappings", () => {
    expect(resolveApp("Calendar", { calendar: "org.fossify.calendar" })).toBe("org.fossify.calendar");
    expect(resolveApp("Mail", { mail: "com.gmailclone" })).toBe("com.gmailclone");
  });

  it("rejects unknown friendly names", () => {
    expect(() => resolveApp("mystery app")).toThrow("Unknown app");
  });
});

describe("summarizeTrajectory", () => {
  it("keeps text and tool activity while dropping image payloads", () => {
    const summary = summarizeTrajectory([
      { role: "user", content: [{ type: "text", text: "do it" }, { type: "image", data: "huge" }] },
      { role: "assistant", content: [{ type: "toolCall", name: "tap", arguments: { x: 1, y: 2 } }] },
    ]);
    expect(summary).toContain("USER: do it");
    expect(summary).toContain("TOOL CALL: tap");
    expect(summary).not.toContain("huge");
  });
});

describe("learning review tools", () => {
  it("reports writes made by review tools", async () => {
    const root = await mkdtemp(join(tmpdir(), "pi-gui-learning-"));
    temporaryDirectories.push(root);
    const writes: string[] = [];
    const store = new LearningStore(root);
    await store.initialize();
    const call = toolCaller(store.createReviewTools(new Set(), writes));
    await call("upsert_skill", {
      mode: "create",
      name: "verified-recovery",
      description: "Recover from a repeatable UI failure",
      body: "Observe and verify the corrected state.",
    });
    expect(writes).toEqual(["skill:created:verified-recovery"]);
  });

  it("saves deduplicated memory entries", async () => {
    const { store, call } = await reviewHarness();
    await call("save_memory", { entries: ["Prefers concise answers", "Prefers concise answers"] });
    await call("save_memory", { entries: ["prefers concise answers", "Uses Android emulators"] });

    const memory = await readFile(store.memoryFile, "utf8");
    expect(memory.match(/concise answers/gi)).toHaveLength(1);
    expect(memory).toContain("Uses Android emulators");
  });

  it("creates, lists, and reads a skill", async () => {
    const { call } = await reviewHarness();
    await call("upsert_skill", {
      mode: "create",
      name: "android-ui-navigation",
      description: "Navigate Android interfaces reliably",
      body: "## Procedure\n\nObserve before every action.",
    });

    expect(await call("list_skills", {})).toContain("android-ui-navigation");
    const skill = await call("read_skill", { name: "android-ui-navigation" });
    expect(skill).toContain("Observe before every action.");
  });

  it("requires read-before-update and permits an update after reading", async () => {
    const first = await reviewHarness();
    await first.call("upsert_skill", {
      mode: "create",
      name: "android-debugging",
      description: "Debug Android UI tasks",
      body: "Check the screenshot.",
    });

    const secondTools = first.store.createReviewTools(new Set());
    const secondCall = toolCaller(secondTools);
    await expect(secondCall("upsert_skill", {
      mode: "update",
      name: "android-debugging",
      description: "Debug Android UI tasks",
      body: "Check visible UI text.",
    })).rejects.toThrow("Read skill 'android-debugging'");

    await secondCall("read_skill", { name: "android-debugging" });
    await secondCall("upsert_skill", {
      mode: "update",
      name: "android-debugging",
      description: "Debug Android UI tasks",
      body: "Check visible UI text.",
    });
    expect(await secondCall("read_skill", { name: "android-debugging" })).toContain("Check visible UI text.");
  });
});

async function reviewHarness() {
  const root = await mkdtemp(join(tmpdir(), "pi-gui-learning-"));
  temporaryDirectories.push(root);
  const store = new LearningStore(root);
  await store.initialize();
  return { store, call: toolCaller(store.createReviewTools()) };
}

function toolCaller(tools: ReturnType<LearningStore["createReviewTools"]> | ReturnType<typeof createAndroidTools>) {
  return async (name: string, params: Record<string, unknown>): Promise<string> => {
    const tool = tools.find((candidate) => candidate.name === name);
    if (!tool) throw new Error(`Missing tool: ${name}`);
    const result = await tool.execute("test", params, undefined, undefined, {} as never);
    return result.content.flatMap((part) => part.type === "text" ? [part.text] : []).join("\n");
  };
}

function staticDevice(): AdbDevice {
  const device = new AdbDevice();
  const image = Buffer.alloc(24);
  Buffer.from("89504e470d0a1a0a", "hex").copy(image);
  image.writeUInt32BE(1080, 16);
  image.writeUInt32BE(2400, 20);
  device.screenSize = async () => ({ width: 1080, height: 2400 });
  device.tap = async () => {};
  device.visibleText = async () => ["Unchanged"];
  device.screenshot = async () => image;
  return device;
}
