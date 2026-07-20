import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { AuthStorage, ModelRegistry } from "@earendil-works/pi-coding-agent";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import type { Context, ImageContent, ThinkingLevel } from "@earendil-works/pi-ai";
import { Command } from "commander";

type Attempt = {
  request: number; attempt: number; mode: string; startedAt: string;
  durationMs: number; firstTokenMs?: number; stopReason: string;
  category: string; error?: string; inputTokens: number; outputTokens: number;
};

function category(stopReason: string, error = ""): string {
  if (stopReason !== "error" && stopReason !== "aborted") return "success";
  if (/terminated/i.test(error)) return "terminated";
  if (/multimodal data is corrupted/i.test(error)) return "multimodal_corrupt";
  if (/timed? out|timeout|aborted/i.test(error)) return "timeout";
  if (/\b429\b|rate.?limit/i.test(error)) return "rate_limit";
  if (/\b5\d\d\b|server.?error|service.?unavailable/i.test(error)) return "server_error";
  return "other_error";
}

async function main(): Promise<void> {
  const cli = new Command()
    .requiredOption("--provider <id>")
    .requiredOption("--model <id>")
    .option("--requests <n>", "logical requests", "20")
    .option("--concurrency <n>", "concurrent logical requests", "4")
    .option("--retries <n>", "same-context retries after the first attempt", "3")
    .option("--thinking <level>", "reasoning level", "high")
    .option("--timeout-ms <n>", "timeout per attempt", "180000")
    .option("--max-tokens <n>", "maximum output tokens", "1024")
    .option("--mode <mode>", "text, image, or mixed", "mixed")
    .option("--image <path>", "JPEG or PNG used by image/mixed mode")
    .option("--output-dir <path>", "result directory", "benchmark-results/provider-stress")
    .parse();
  const o = cli.opts();
  const requests = positive(o.requests, "requests");
  const concurrency = positive(o.concurrency, "concurrency");
  const retries = nonnegative(o.retries, "retries");
  const timeoutMs = positive(o.timeoutMs, "timeout-ms");
  const maxTokens = positive(o.maxTokens, "max-tokens");
  if (!['text', 'image', 'mixed'].includes(o.mode)) throw new Error("--mode must be text, image, or mixed");
  if (o.mode !== "text" && !o.image) throw new Error("--image is required for image or mixed mode");

  const auth = AuthStorage.create();
  const registry = ModelRegistry.create(auth);
  const model = registry.find(o.provider, o.model);
  if (!model) throw new Error(`Unknown model ${o.provider}/${o.model}`);
  const selectedModel = model;
  const apiKey = await auth.getApiKey(o.provider);
  if (!apiKey) throw new Error(`No API key configured for provider ${o.provider}`);
  const imageData = o.image ? await readFile(resolve(o.image)) : undefined;
  const imageMime = o.image?.toLowerCase().endsWith(".png") ? "image/png" : "image/jpeg";
  const attempts: Attempt[] = [];
  let next = 0;

  async function worker(): Promise<void> {
    while (true) {
      const request = next++;
      if (request >= requests) return;
      const mode = o.mode === "mixed" ? (request % 2 ? "image" : "text") : o.mode;
      const image: ImageContent | undefined = imageData
        ? { type: "image", data: imageData.toString("base64"), mimeType: imageMime }
        : undefined;
      const context: Context = {
        systemPrompt: "Return a concise response. This is a provider reliability test.",
        messages: [{
          role: "user", timestamp: Date.now(),
          content: mode === "image"
            ? [{ type: "text", text: `Request ${request}: describe the image, then output OK.` }, image!]
            : `Request ${request}: compute 137 * 29, briefly explain, then output OK.`,
        }],
      };
      for (let attempt = 0; attempt <= retries; attempt++) {
        const started = Date.now();
        let firstTokenMs: number | undefined;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        let final;
        try {
          const stream = streamSimple(selectedModel, context, {
            apiKey, reasoning: o.thinking as ThinkingLevel, maxTokens,
            maxRetries: 0, signal: controller.signal,
          });
          for await (const event of stream) {
            if (firstTokenMs === undefined && (event.type === "text_delta" || event.type === "thinking_delta")) {
              firstTokenMs = Date.now() - started;
            }
          }
          final = await stream.result();
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          final = { stopReason: controller.signal.aborted ? "aborted" : "error", errorMessage: message,
            usage: { input: 0, output: 0 } };
        } finally {
          clearTimeout(timer);
        }
        const error = final.errorMessage?.slice(0, 2000);
        const row: Attempt = {
          request, attempt: attempt + 1, mode, startedAt: new Date(started).toISOString(),
          durationMs: Date.now() - started, firstTokenMs, stopReason: final.stopReason,
          category: category(final.stopReason, error), error,
          inputTokens: final.usage?.input ?? 0, outputTokens: final.usage?.output ?? 0,
        };
        attempts.push(row);
        process.stdout.write(JSON.stringify(row) + "\n");
        if (row.category === "success") break;
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, requests) }, () => worker()));
  const outputDir = resolve(o.outputDir);
  await mkdir(outputDir, { recursive: true });
  const finalByRequest = Array.from({ length: requests }, (_, request) => attempts.filter(a => a.request === request).at(-1)!);
  const counts = Object.fromEntries([...new Set(attempts.map(a => a.category))].map(k => [k, attempts.filter(a => a.category === k).length]));
  const summary = {
    generatedAt: new Date().toISOString(), provider: o.provider, model: o.model,
    thinking: o.thinking, requests, concurrency, retries, timeoutMs, maxTokens, mode: o.mode,
    logicalSuccesses: finalByRequest.filter(a => a.category === "success").length,
    logicalFailures: finalByRequest.filter(a => a.category !== "success").length,
    attemptCounts: counts, attempts,
  };
  await writeFile(resolve(outputDir, "results.json"), JSON.stringify(summary, null, 2) + "\n");
  const md = `# Provider stress result\n\n- Provider: \`${o.provider}/${o.model}\`\n- Requests: ${requests}\n- Concurrency: ${concurrency}\n- Same-context retries: ${retries}\n- Successes: ${summary.logicalSuccesses}\n- Failures: ${summary.logicalFailures}\n- Attempt categories: \`${JSON.stringify(counts)}\`\n`;
  await writeFile(resolve(outputDir, "results.md"), md);
}

function positive(value: string, name: string): number {
  const n = Number(value); if (!Number.isInteger(n) || n < 1) throw new Error(`--${name} must be a positive integer`); return n;
}
function nonnegative(value: string, name: string): number {
  const n = Number(value); if (!Number.isInteger(n) || n < 0) throw new Error(`--${name} must be a nonnegative integer`); return n;
}

await main();
