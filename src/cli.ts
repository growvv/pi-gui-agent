#!/usr/bin/env node
import { Command, InvalidArgumentError } from "commander";
import { writeFile } from "node:fs/promises";
import { runTask } from "./agent.js";

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
  .option("--settle-ms <number>", "delay after each action", nonNegativeNumber, 1500)
  .option("--result-file <path>", "write the final machine-readable result")
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
      settleMs: flags.settleMs,
      learning: flags.learning,
      onText: (text) => process.stdout.write(text),
      onLearningError: (error) => console.error(`Learning review failed: ${String(error)}`),
    });
    if (flags.resultFile) {
      await writeFile(
        flags.resultFile,
        JSON.stringify({ finished: state.finished, answer: state.answer }),
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
