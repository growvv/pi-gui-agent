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

export interface AdbOptions {
  adbPath?: string;
  serial?: string;
  timeoutMs?: number;
  onWarning?: (message: string) => void;
}

export class AdbDevice {
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
    return (await this.run(["exec-out", "screencap", "-p"], "buffer")) as Buffer;
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
    const nodes = await this.uiNodes();
    return [...new Set(nodes.flatMap((node) => [node.text, node["content-desc"]])
      .filter((value): value is string => typeof value === "string" && value.trim().length > 0))];
  }

  private async uiNodes(): Promise<Record<string, unknown>[]> {
    await this.shell("uiautomator", "dump", "/sdcard/pi-gui-window.xml");
    const xml = await this.shell("cat", "/sdcard/pi-gui-window.xml");
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
