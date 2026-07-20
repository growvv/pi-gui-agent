import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { XMLParser } from "fast-xml-parser";

const execFileAsync = promisify(execFile);
const ADB_KEYBOARD_PACKAGE = "com.android.adbkeyboard";
const ADB_KEYBOARD_IME = `${ADB_KEYBOARD_PACKAGE}/.AdbIME`;

export interface ScreenSize {
  width: number;
  height: number;
}

export interface UiElement {
  index: number;
  text?: string;
  contentDesc?: string;
  resourceId?: string;
  className?: string;
  clickable: boolean;
  enabled: boolean;
  bounds: string;
  center: { x: number; y: number };
}

export interface AdbOptions {
  adbPath?: string;
  serial?: string;
  timeoutMs?: number;
  onWarning?: (message: string) => void;
}

export interface AndroidDevice {
  screenshot(): Promise<Buffer>;
  screenSize(): Promise<ScreenSize>;
  tap(x: number, y: number): Promise<void>;
  swipe(x1: number, y1: number, x2: number, y2: number, durationMs?: number): Promise<void>;
  typeText(text: string): Promise<void>;
  key(keyCode: string): Promise<void>;
  openApp(name: string): Promise<void>;
  uiElements(): Promise<UiElement[]>;
  clickElement(element: UiElement): Promise<void>;
  restoreInputMethod(): Promise<void>;
}

export class AdbDevice implements AndroidDevice {
  readonly adbPath: string;
  readonly serial?: string;
  readonly timeoutMs: number;
  readonly onWarning: (message: string) => void;
  private textInputSetup?: Promise<boolean>;
  private previousInputMethod?: { ime?: string; adbKeyboardWasEnabled: boolean };
  private warnedAboutTextFallback = false;

  constructor(options: AdbOptions = {}) {
    this.adbPath = options.adbPath ?? "adb";
    this.serial = options.serial;
    this.timeoutMs = options.timeoutMs ?? 15_000;
    this.onWarning = options.onWarning ?? ((message) => console.warn(message));
  }

  async run(args: string[], encoding: BufferEncoding | "buffer" = "utf8"): Promise<string | Buffer> {
    const adbArgs = this.serial ? ["-s", this.serial, ...args] : args;
    const { stdout } = await execFileAsync(this.adbPath, adbArgs, {
      encoding: encoding === "buffer" ? "buffer" : encoding,
      timeout: this.timeoutMs,
      maxBuffer: 20 * 1024 * 1024,
    });
    return stdout;
  }

  async shell(...args: string[]): Promise<string> {
    return (await this.run(["shell", ...args])) as string;
  }

  async screenshot(): Promise<Buffer> {
    return this.retry(async () => {
      const image = (await this.run(["exec-out", "screencap", "-p"], "buffer")) as Buffer;
      const signature = "89504e470d0a1a0a";
      const trailer = "0000000049454e44ae426082";
      if (image.length < 24 || image.subarray(0, 8).toString("hex") !== signature ||
          image.subarray(-12).toString("hex") !== trailer) {
        throw new Error(`ADB returned an incomplete PNG (${image.length} bytes)`);
      }
      return image;
    }, "screenshot");
  }

  async screenSize(): Promise<ScreenSize> {
    const image = await this.screenshot();
    const pngSignature = "89504e470d0a1a0a";
    if (image.length < 24 || image.subarray(0, 8).toString("hex") !== pngSignature) {
      throw new Error("Could not read screen size from screenshot");
    }
    return { width: image.readUInt32BE(16), height: image.readUInt32BE(20) };
  }

  async tap(x: number, y: number): Promise<void> {
    await this.shell("input", "tap", String(x), String(y));
  }

  async swipe(x1: number, y1: number, x2: number, y2: number, durationMs = 400): Promise<void> {
    await this.shell("input", "swipe", String(x1), String(y1), String(x2), String(y2), String(durationMs));
  }

  async typeText(text: string): Promise<void> {
    if (!await this.hasFocusedEditableNode()) {
      throw new Error("No editable text field is focused. Tap the intended field, verify that it has focus, then call type_text again.");
    }
    if (await this.prepareAdbKeyboard()) {
      const encoded = Buffer.from(text, "utf8").toString("base64");
      await this.shell("am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded);
      return;
    }
    if (!this.warnedAboutTextFallback) {
      this.onWarning("ADB Keyboard is not installed; falling back to adb shell input text, which may not preserve Unicode, newlines, or special characters");
      this.warnedAboutTextFallback = true;
    }
    const encoded = text.replace(/%/g, "%25").replace(/ /g, "%s");
    await this.shell("input", "text", shellQuote(encoded));
  }

  async restoreInputMethod(): Promise<void> {
    const previous = this.previousInputMethod;
    this.previousInputMethod = undefined;
    this.textInputSetup = undefined;
    this.warnedAboutTextFallback = false;
    if (!previous) return;
    if (previous.ime) await this.shell("ime", "set", previous.ime);
    if (!previous.adbKeyboardWasEnabled) await this.shell("ime", "disable", ADB_KEYBOARD_IME);
  }

  async key(keyCode: string): Promise<void> {
    await this.shell("input", "keyevent", keyCode);
  }

  async openApp(packageName: string): Promise<void> {
    await this.shell("monkey", "-p", packageName, "-c", "android.intent.category.LAUNCHER", "1");
  }

  async visibleText(): Promise<string[]> {
    const elements = await this.uiElements();
    return [...new Set(elements.flatMap((element) => [element.text, element.contentDesc])
      .filter((value): value is string => typeof value === "string" && value.trim().length > 0))];
  }

  async uiElements(): Promise<UiElement[]> {
    const nodes = await this.uiNodes();
    return nodes
      .filter((node) => typeof node.bounds === "string" && (
        node.clickable === "true" || hasText(node.text) || hasText(node["content-desc"]) ||
        hasText(node["resource-id"])
      ))
      .map((node, index) => {
        const bounds = node.bounds as string;
        return {
          index,
          text: optionalText(node.text),
          contentDesc: optionalText(node["content-desc"]),
          resourceId: optionalText(node["resource-id"]),
          className: optionalText(node.class),
          clickable: node.clickable === "true",
          enabled: node.enabled !== "false",
          bounds,
          center: boundsCenter(bounds),
        };
      });
  }

  async clickElement(element: UiElement): Promise<void> {
    if (!element.enabled) throw new Error(`UI element [${element.index}] is disabled`);
    if (!element.clickable) throw new Error(`UI element [${element.index}] is not clickable`);
    await this.tap(element.center.x, element.center.y);
  }

  async hasFocusedEditableNode(): Promise<boolean> {
    const nodes = await this.uiNodes();
    return nodes.some((node) => node.focused === "true" && (
      node.editable === "true" ||
      (typeof node.class === "string" && /(?:EditText|AutoCompleteTextView)$/.test(node.class))
    ));
  }

  private async uiNodes(): Promise<Record<string, unknown>[]> {
    const xml = await this.retry(async () => {
      await this.shell("rm", "-f", "/sdcard/pi-gui-window.xml");
      await this.shell("uiautomator", "dump", "/sdcard/pi-gui-window.xml");
      const value = await this.shell("cat", "/sdcard/pi-gui-window.xml");
      if (!value.includes("<hierarchy")) throw new Error("UI hierarchy is incomplete");
      return value;
    }, "UI hierarchy");
    const root = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "" }).parse(xml);
    const nodes: Record<string, unknown>[] = [];
    const visit = (value: unknown): void => {
      if (Array.isArray(value)) return value.forEach(visit);
      if (!value || typeof value !== "object") return;
      const node = value as Record<string, unknown>;
      if (typeof node.bounds === "string") nodes.push(node);
      Object.values(node).forEach(visit);
    };
    visit(root);
    return nodes;
  }

  private async retry<T>(operation: () => Promise<T>, label: string): Promise<T> {
    let lastError: unknown;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        return await operation();
      } catch (error) {
        lastError = error;
        if (attempt === 3) break;
        this.onWarning(`${label} failed (attempt ${attempt}/3); waiting for ADB device`);
        try { await this.run(["wait-for-device"]); } catch { /* retry reports final error */ }
        await new Promise((resolve) => setTimeout(resolve, 300 * attempt));
      }
    }
    throw lastError;
  }

  private async prepareAdbKeyboard(): Promise<boolean> {
    return this.textInputSetup ??= this.setupAdbKeyboard();
  }

  private async setupAdbKeyboard(): Promise<boolean> {
    const packagePath = await this.shell("pm", "path", ADB_KEYBOARD_PACKAGE);
    if (!packagePath.trim()) return false;

    const [current, enabled] = await Promise.all([
      this.shell("settings", "get", "secure", "default_input_method"),
      this.shell("ime", "list", "-s"),
    ]);
    const currentIme = current.trim();
    this.previousInputMethod = {
      ime: currentIme && currentIme !== "null" ? currentIme : undefined,
      adbKeyboardWasEnabled: enabled.split(/\s+/).includes(ADB_KEYBOARD_IME),
    };
    if (!this.previousInputMethod.adbKeyboardWasEnabled) {
      await this.shell("ime", "enable", ADB_KEYBOARD_IME);
    }
    if (currentIme !== ADB_KEYBOARD_IME) await this.shell("ime", "set", ADB_KEYBOARD_IME);
    return true;
  }
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function hasText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function optionalText(value: unknown): string | undefined {
  return hasText(value) ? value : undefined;
}

export function boundsCenter(bounds: string): { x: number; y: number } {
  const match = /^\[(\d+),(\d+)]\[(\d+),(\d+)]$/.exec(bounds);
  if (!match) throw new Error(`Invalid UI element bounds: ${bounds}`);
  const [, x1, y1, x2, y2] = match.map(Number);
  return {
    x: Math.round((x1! + x2!) / 2),
    y: Math.round((y1! + y2!) / 2),
  };
}
