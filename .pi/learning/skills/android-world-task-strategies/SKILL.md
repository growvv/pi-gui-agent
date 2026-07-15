---
name: android-world-task-strategies
description: "Reliable AndroidWorld procedures for Chrome onboarding, Audio Recorder controls, exact-duplicate decisions, and multi-screen information retrieval or arithmetic tasks in Gallery and Joplin."
metadata:
  created_by: pi-gui-agent
  knowledge_source: mobile-agent-v3.5
---

# AndroidWorld Task Strategies

Apply only the section relevant to the current task and verify that the visible UI matches before acting.

## Chrome first run

1. Open Chrome with `open_app`.
2. If onboarding appears, prefer **Use without an account**.
3. On the alternate onboarding flow, choose **Accept & continue**, then **No thanks** when asked about sign-in or sync.
4. Never add an account or sign in. Verify that Chrome reaches a usable browser page before continuing.

## Audio Recorder

- When stopping an active recording, look for the white square stop icon, normally the fourth control from the left along the bottom.
- Do not confuse it with the circular pause control in the center.
- Treat these positions as identification hints, not fixed coordinates; confirm the icons on the current screenshot.

## Exact duplicates

- Consider two notes, files, or records exact duplicates only when their name, creation date/time, and detailed content all match.
- Do not delete an item merely because its name matches another item.
- Inspect details when the list view does not expose every required field.

## Information collection

- For questions spanning multiple screens or items, record each verified fact before navigating away. Do not rely on remembering screenshots that may leave the active context.
- For Simple Gallery transaction tasks, use only transactions found under `DCIM` unless the task explicitly names another location.
- In Joplin, especially under an `Ideas` folder, record only text visibly confirmed on screen; do not infer missing entries or manufacture notes from the task wording.
- For tasks asking for a product, record every encountered number, confirm that all required items were visited, and only then calculate the product.
- After gathering all required evidence, call `answer` with the concise result and then call `finish`.
