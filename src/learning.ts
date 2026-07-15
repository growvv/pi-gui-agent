import { mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import type { Model } from "@earendil-works/pi-ai";
import {
  type AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  type ModelRegistry,
  SessionManager,
  type SettingsManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const REVIEW_SYSTEM_PROMPT = `You maintain durable memory and procedural skills from an agent trajectory.

Memory records who the user is: stable preferences, expectations, and personal facts. Skills record how to perform a reusable class of task.

Rules:
- Save only durable, useful learning. Ignore one-off task details and transient environment failures.
- Prefer updating an existing broad skill over creating a narrow session-specific skill.
- Before updating a skill, list and read it. Encode corrections, reliable techniques, and pitfalls.
- Skill names must be lowercase kebab-case and class-level, not an error string or today's task.
- If nothing durable was learned, make no tool calls and respond exactly: Nothing to save.`;

export interface LearningOptions {
  root?: string;
  model: Model<any>;
  authStorage: AuthStorage;
  modelRegistry: ModelRegistry;
  settingsManager: SettingsManager;
}

export class LearningStore {
  readonly root: string;
  readonly skillsDir: string;
  readonly memoryFile: string;

  constructor(root = ".pi/learning") {
    this.root = resolve(root);
    this.skillsDir = join(this.root, "skills");
    this.memoryFile = join(this.root, "MEMORY.md");
  }

  async initialize(): Promise<void> {
    await mkdir(this.skillsDir, { recursive: true });
  }

  async memoryPrompt(): Promise<string | undefined> {
    const content = await readOptional(this.memoryFile);
    return content ? `Durable memory from previous tasks:\n${content}` : undefined;
  }

  async review(trajectory: string, options: LearningOptions): Promise<void> {
    await this.initialize();
    const readSkills = new Set<string>();
    const tools = this.createReviewTools(readSkills);
    const loader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: dirname(this.root),
      settingsManager: options.settingsManager,
      noExtensions: true,
      noSkills: true,
      noPromptTemplates: true,
      noContextFiles: true,
      systemPrompt: REVIEW_SYSTEM_PROMPT,
    });
    await loader.reload();
    const { session } = await createAgentSession({
      cwd: process.cwd(),
      model: options.model,
      thinkingLevel: "low",
      noTools: "all",
      customTools: tools,
      resourceLoader: loader,
      sessionManager: SessionManager.inMemory(process.cwd()),
      settingsManager: options.settingsManager,
      authStorage: options.authStorage,
      modelRegistry: options.modelRegistry,
    });
    session.setActiveToolsByName(tools.map((tool) => tool.name));
    try {
      await session.prompt(`Review this completed task trajectory:\n\n${trajectory}`);
    } finally {
      session.dispose();
    }
  }

  createReviewTools(readSkills = new Set<string>()): ToolDefinition[] {
    return [
      {
        name: "save_memory",
        label: "Save memory",
        description: "Append durable user facts or preferences to memory.",
        parameters: Type.Object({ entries: Type.Array(Type.String({ minLength: 1 }), { minItems: 1, maxItems: 10 }) }),
        execute: async (_id, { entries }) => {
          const existing = (await readOptional(this.memoryFile)) ?? "# Memory\n";
          const known = new Set(existing.split("\n").map(normalizeMemory));
          const added: string[] = [];
          for (const rawEntry of entries as string[]) {
            const entry = rawEntry.trim();
            const normalized = normalizeMemory(entry);
            if (!entry || known.has(normalized)) continue;
            known.add(normalized);
            added.push(entry);
          }
          if (added.length) await atomicWrite(this.memoryFile, `${existing.trimEnd()}\n${added.map((entry) => `- ${entry}`).join("\n")}\n`);
          return textResult(added.length ? `Saved ${added.length} memory entries.` : "Memory already contained these entries.");
        },
      },
      {
        name: "list_skills",
        label: "List skills",
        description: "List existing learned skills before deciding whether to create or update one.",
        parameters: Type.Object({}),
        execute: async () => {
          const names = await this.skillNames();
          return textResult(names.length ? names.join("\n") : "No learned skills yet.");
        },
      },
      {
        name: "read_skill",
        label: "Read skill",
        description: "Read an existing learned skill. Required before updating it.",
        parameters: Type.Object({ name: Type.String() }),
        execute: async (_id, { name }) => {
          validateSkillName(name);
          const content = await readOptional(join(this.skillsDir, name, "SKILL.md"));
          if (!content) throw new Error(`Skill '${name}' does not exist.`);
          readSkills.add(name);
          return textResult(content);
        },
      },
      {
        name: "upsert_skill",
        label: "Create or update skill",
        description: "Create a reusable skill or replace one that was read first.",
        parameters: Type.Object({
          mode: Type.Union([Type.Literal("create"), Type.Literal("update")]),
          name: Type.String({ description: "Lowercase kebab-case skill name" }),
          description: Type.String({ minLength: 1, maxLength: 300 }),
          body: Type.String({ minLength: 1, description: "Actionable Markdown procedure without frontmatter" }),
        }),
        execute: async (_id, { mode, name, description, body }) => {
          validateSkillName(name);
          const path = join(this.skillsDir, name, "SKILL.md");
          const exists = Boolean(await readOptional(path));
          if (mode === "create" && exists) throw new Error(`Skill '${name}' already exists; read and update it.`);
          if (mode === "update" && !readSkills.has(name)) throw new Error(`Read skill '${name}' before updating it.`);
          const content = `---\nname: ${name}\ndescription: ${yamlString(description)}\nmetadata:\n  created_by: pi-gui-agent\n---\n\n${body.trim()}\n`;
          await atomicWrite(path, content);
          return textResult(`${exists ? "Updated" : "Created"} skill '${name}'.`);
        },
      },
    ] as ToolDefinition[];
  }

  private async skillNames(): Promise<string[]> {
    const entries = await readdir(this.skillsDir, { withFileTypes: true });
    return entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  }
}

export function summarizeTrajectory(messages: readonly unknown[], maxChars = 30_000): string {
  const lines: string[] = [];
  for (const message of messages as Array<Record<string, unknown>>) {
    const role = typeof message.role === "string" ? message.role : "unknown";
    const content = message.content;
    if (typeof content === "string") lines.push(`${role.toUpperCase()}: ${content}`);
    if (!Array.isArray(content)) continue;
    for (const part of content as Array<Record<string, unknown>>) {
      if (part.type === "text" && typeof part.text === "string") lines.push(`${role.toUpperCase()}: ${part.text}`);
      if (part.type === "toolCall") lines.push(`TOOL CALL: ${String(part.name)} ${JSON.stringify(part.arguments ?? {})}`);
      if (part.type === "toolResult") lines.push(`TOOL RESULT: ${compactText(part.content)}`);
    }
  }
  const result = lines.join("\n");
  return result.length <= maxChars ? result : `[Earlier trajectory omitted]\n${result.slice(-maxChars)}`;
}

function compactText(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value.flatMap((part) => typeof part === "object" && part && "text" in part ? [String(part.text)] : []).join(" ").slice(0, 1000);
}

function normalizeMemory(value: string): string {
  return value.replace(/^[-*]\s*/, "").trim().toLowerCase();
}

function validateSkillName(name: string): void {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) || name.length > 64) throw new Error("Skill name must be lowercase kebab-case (max 64 characters). ");
}

function yamlString(value: string): string {
  return JSON.stringify(value.trim());
}

async function readOptional(path: string): Promise<string | undefined> {
  try { return await readFile(path, "utf8"); } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

async function atomicWrite(path: string, content: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  await writeFile(temporary, content, "utf8");
  await rename(temporary, path);
}

function textResult(text: string) {
  return { content: [{ type: "text" as const, text }], details: {} };
}
