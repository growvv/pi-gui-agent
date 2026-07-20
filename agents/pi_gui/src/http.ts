import type { AndroidDevice, ScreenSize, UiElement } from "./adb.js";

interface HttpDeviceOptions {
  serverUrl: string;
  timeoutMs?: number;
}

interface StateResponse {
  width: number;
  height: number;
  elements: Array<{
    index: number;
    text?: string;
    content_description?: string;
    resource_id?: string;
    class_name?: string;
    is_clickable?: boolean;
    is_enabled?: boolean;
    bounds: string;
    center: { x: number; y: number };
  }>;
}

const APP_ALIASES: Record<string, string> = {
  "com.android.camera2": "camera",
  "com.android.chrome": "chrome",
  "com.android.settings": "settings",
  "com.arduia.expense": "pro expense",
  "com.dimowner.audiorecorder": "audio recorder",
  "com.flauschcode.broccoli": "broccoli",
  "com.google.android.contacts": "contacts",
  "com.google.android.deskclock": "clock",
  "com.google.android.documentsui": "files",
  "com.simplemobiletools.calendar.pro": "simple calendar pro",
  "com.simplemobiletools.draw.pro": "simple draw pro",
  "com.simplemobiletools.gallery.pro": "simple gallery pro",
  "com.simplemobiletools.smsmessenger": "simple sms messenger",
  "code.name.monkey.retromusic": "retro music",
  "de.dennisguse.opentracks": "opentracks",
  "net.gsantner.markor": "markor",
  "net.osmand": "osmand",
  "org.tasks": "tasks",
  "org.videolan.vlc": "vlc",
};

export class HttpDevice implements AndroidDevice {
  readonly serverUrl: string;
  readonly timeoutMs: number;

  constructor(options: HttpDeviceOptions) {
    this.serverUrl = options.serverUrl.replace(/\/$/, "");
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  async screenshot(): Promise<Buffer> {
    const response = await this.request("/pi/screenshot?wait_to_stabilize=false");
    return Buffer.from(await response.arrayBuffer());
  }

  async screenSize(): Promise<ScreenSize> {
    const state = await this.state();
    return { width: state.width, height: state.height };
  }

  async tap(x: number, y: number): Promise<void> {
    await this.action({ action_type: "click", x, y });
  }

  async swipe(x1: number, y1: number, x2: number, y2: number, durationMs = 400): Promise<void> {
    await this.request("/pi/swipe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ x1, y1, x2, y2, duration_ms: durationMs }),
    });
  }

  async typeText(text: string): Promise<void> {
    await this.action({ action_type: "input_text", text, clear_text: false });
  }

  async key(keyCode: string): Promise<void> {
    if (keyCode !== "KEYCODE_BACK") {
      throw new Error(`FastAPI transport does not support key ${keyCode}`);
    }
    await this.action({ action_type: "navigate_back" });
  }

  async openApp(name: string): Promise<void> {
    await this.action({ action_type: "open_app", app_name: APP_ALIASES[name] ?? name });
  }

  async uiElements(): Promise<UiElement[]> {
    const state = await this.state();
    return state.elements.map((element) => ({
      index: element.index,
      text: element.text,
      contentDesc: element.content_description,
      resourceId: element.resource_id,
      className: element.class_name,
      clickable: element.is_clickable === true,
      enabled: element.is_enabled !== false,
      bounds: element.bounds,
      center: element.center,
    }));
  }

  async clickElement(element: UiElement): Promise<void> {
    await this.action({ action_type: "click", index: element.index });
  }

  async restoreInputMethod(): Promise<void> {}

  private async state(): Promise<StateResponse> {
    const response = await this.request("/pi/state?wait_to_stabilize=false");
    return await response.json() as StateResponse;
  }

  private async action(action: Record<string, unknown>): Promise<void> {
    await this.request("/execute_action", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(action),
    });
  }

  private async request(path: string, init?: RequestInit): Promise<Response> {
    const response = await fetch(`${this.serverUrl}${path}`, {
      ...init,
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    if (!response.ok) {
      throw new Error(`AndroidWorld server ${response.status}: ${await response.text()}`);
    }
    return response;
  }
}
