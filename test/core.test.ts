import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { AdbDevice } from "../src/adb.js";
import { resolveApp } from "../src/apps.js";
import { normalizedPoint } from "../src/tools.js";
import { LearningStore, summarizeTrajectory } from "../src/learning.js";

const temporaryDirectories: string[] = [];

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

  it("warns once and falls back to input text when ADB Keyboard is unavailable", async () => {
    const warnings: string[] = [];
    const device = new AdbDevice({ onWarning: (message) => warnings.push(message) });
    const calls: string[][] = [];
    device.shell = async (...received) => {
      calls.push(received);
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

describe("resolveApp", () => {
  it("resolves Android World names", () => {
    expect(resolveApp("Simple Calendar Pro")).toBe("com.simplemobiletools.calendar.pro");
    expect(resolveApp("Markor")).toBe("net.gsantner.markor");
  });

  it("accepts a package name", () => {
    expect(resolveApp("com.example.app")).toBe("com.example.app");
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

function toolCaller(tools: ReturnType<LearningStore["createReviewTools"]>) {
  return async (name: string, params: Record<string, unknown>): Promise<string> => {
    const tool = tools.find((candidate) => candidate.name === name);
    if (!tool) throw new Error(`Missing tool: ${name}`);
    const result = await tool.execute("test", params, undefined, undefined, {} as never);
    return result.content.flatMap((part) => part.type === "text" ? [part.text] : []).join("\n");
  };
}
