export const APPS: Record<string, string> = {
  "audio recorder": "com.dimowner.audiorecorder",
  broccoli: "com.flauschcode.broccoli",
  calendar: "com.simplemobiletools.calendar.pro",
  camera: "com.android.camera2",
  chrome: "com.android.chrome",
  clock: "com.google.android.deskclock",
  contacts: "com.google.android.contacts",
  files: "com.google.android.documentsui",
  gallery: "com.simplemobiletools.gallery.pro",
  joplin: "net.cozic.joplin",
  markor: "net.gsantner.markor",
  messages: "com.simplemobiletools.smsmessenger",
  opentracks: "de.dennisguse.opentracks",
  osmand: "net.osmand",
  "pro expense": "com.arduia.expense",
  recipes: "com.flauschcode.broccoli",
  "retro music": "code.name.monkey.retromusic",
  settings: "com.android.settings",
  "simple calendar": "com.simplemobiletools.calendar.pro",
  "simple calendar pro": "com.simplemobiletools.calendar.pro",
  "simple draw pro": "com.simplemobiletools.draw.pro",
  "simple gallery": "com.simplemobiletools.gallery.pro",
  "simple gallery pro": "com.simplemobiletools.gallery.pro",
  "simple sms": "com.simplemobiletools.smsmessenger",
  "simple sms messenger": "com.simplemobiletools.smsmessenger",
  tasks: "org.tasks",
  vlc: "org.videolan.vlc",
};

export function resolveApp(name: string): string {
  const normalized = name.toLowerCase().trim();
  if (normalized.includes(".")) return normalized;
  const packageName = APPS[normalized];
  if (!packageName) throw new Error(`Unknown app '${name}'. Pass its Android package name instead.`);
  return packageName;
}
