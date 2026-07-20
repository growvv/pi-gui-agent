#!/usr/bin/env node
import { Command, InvalidArgumentError } from "commander";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { runTask } from "./agent.js";

const defaultLearningRoot = fileURLToPath(new URL("../../../.pi/learning", import.meta.url));

const program = new Command()
  .name("pi-gui-agent")
  .description("Operate an Android device with pi-coding-agent")
  .argument("<task>", "task to perform on the phone")
  .option("--serial <serial>", "ADB device serial")
  .option("--adb <path>", "path to adb", "adb")
  .option("--provider <provider>", "pi model provider")
  .option("--model <model>", "pi model id")
  .option("--thinking <level>", "off|minimal|low|medium|high|xhigh", "medium")
  .option("--max-actions <number>", "maximum device-changing actions", positiveInteger, 30)
  .option("--max-no-progress-actions <number>", "warn and replan after this many unchanged or repeated actions", positiveInteger, 4)
  .option("--max-model-tokens <number>", "maximum output tokens for each model turn", positiveInteger, 4096)
  .option("--settle-ms <number>", "delay after each action", nonNegativeNumber, 1500)
  .option("--result-file <path>", "write the final machine-readable result")
  .option("--session-dir <path>", "directory for pi session logs")
  .option("--ledger-dir <path>", "directory for managed execution ledgers", ".pi/ledgers")
  .option("--learning-root <path>", "directory for persistent memory and skills", defaultLearningRoot)
  .option("--app-map <json>", "benchmark-specific friendly app name mapping", appMap, {})
  .option("--no-learning", "disable post-task memory and skill review")
  .action(async (task, flags) => {
    if (Boolean(flags.provider) !== Boolean(flags.model)) {
      throw new Error("--provider and --model must be supplied together");
    }
    const state = await runTask(task, {
      adbPath: flags.adb,
      serial: flags.serial,
      provider: flags.provider,
      model: flags.model,
      thinking: flags.thinking,
      maxActions: flags.maxActions,
      maxNoProgressActions: flags.maxNoProgressActions,
      maxModelTokens: flags.maxModelTokens,
      settleMs: flags.settleMs,
      learning: flags.learning,
      sessionDir: flags.sessionDir,
      ledgerDir: flags.ledgerDir,
      learningRoot: flags.learningRoot,
      appMap: flags.appMap,
      onText: (text) => process.stdout.write(text),
      onLearningError: (error) => console.error(`Learning review failed: ${String(error)}`),
      onLearningReview: (status, writes) => console.error(
        status === "saved" ? `Learning review saved: ${writes.join(", ")}` : "Learning review skipped: no durable item",
      ),
    });
    if (flags.resultFile) {
      await writeFile(
        flags.resultFile,
        JSON.stringify({
          finished: state.finished,
          answer: state.answer,
          actions: state.actions,
          progressWarnings: state.progressWarnings ?? 0,
          stalled: state.stalled ?? false,
          ledgerPath: state.ledgerPath,
        }),
        "utf8",
      );
    }
    process.stdout.write("\n");
    if (state.answer) console.log(`Answer: ${state.answer}`);
    if (!state.finished) process.exitCode = 2;
  });

await program.parseAsync();

function positiveInteger(value: string): number {
  const number = Number(value);
  if (!Number.isInteger(number) || number <= 0) {
    throw new InvalidArgumentError("must be a positive integer");
  }
  return number;
}

function nonNegativeNumber(value: string): number {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) {
    throw new InvalidArgumentError("must be a finite non-negative number");
  }
  return number;
}

function appMap(value: string): Record<string, string> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new InvalidArgumentError("must be a JSON object of app names to package names");
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new InvalidArgumentError("must be a JSON object of app names to package names");
  }
  const entries = Object.entries(parsed);
  if (entries.some(([name, packageName]) => !name.trim() || typeof packageName !== "string" || !packageName.includes("."))) {
    throw new InvalidArgumentError("must map non-empty app names to Android package names");
  }
  return Object.fromEntries(entries.map(([name, packageName]) => [name.toLowerCase().trim(), packageName as string]));
}
