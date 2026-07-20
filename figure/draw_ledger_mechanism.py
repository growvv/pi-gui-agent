#!/usr/bin/env python3
"""Draw two evidence-backed ledger mechanism examples for the paper."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figure" / "ledger_mechanism_examples"

TASKS = ROOT / "benchmark-results/androidworld-all/tasks/TasksHighPriorityTasksDueOnDate/screenshots"
MAZE = ROOT / "benchmark-results/androidworld-all/tasks/BrowserMaze/screenshots"

IMAGES = {
    "wrong_sort": TASKS / "action-0007-2026-07-20T06-03-42-442Z-eec4922444f4.png",
    "sort_menu": TASKS / "action-0008-2026-07-20T06-03-58-433Z-a39bee043112.png",
    "priority_result": TASKS / "action-0011-2026-07-20T06-04-32-644Z-26b869a9e3fd.png",
    "maze_before": MAZE / "action-0012-2026-07-20T07-32-52-159Z-c7a15da947e1.png",
    "maze_success": MAZE / "action-0013-2026-07-20T07-33-03-809Z-854a59773cb1.png",
}

C_BLUE = "#3f73c8"
C_ORANGE = "#f2a007"
C_GREEN = "#48a82e"
C_LIGHT_GREEN = "#eaf5df"
C_RED = "#d83b32"
C_TEXT = "#171717"
C_MUTED = "#657078"
C_LINE = "#8fa0a4"
C_CODE = "#f7f8fa"


fig, ax = plt.subplots(figsize=(20, 14.2))
fig.patch.set_facecolor("white")
ax.set_xlim(0, 20)
ax.set_ylim(0, 14.2)
ax.axis("off")


def rounded(x, y, w, h, edge, face="white", lw=1.7, radius=0.13, z=2):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.08,rounding_size={radius}",
        facecolor=face,
        edgecolor=edge,
        linewidth=lw,
        zorder=z,
    )
    ax.add_patch(patch)
    return patch


def arrow(x1, y1, x2, y2, color=C_ORANGE, lw=2.2):
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            arrowstyle="-|>",
            mutation_scale=16,
            linewidth=lw,
            color=color,
            zorder=8,
        )
    )


def phone(path, x, y, h, label, highlight=None, highlight_color=C_GREEN):
    """Render a full screenshot at its native aspect ratio without cropping."""
    image = plt.imread(path)
    ih, iw = image.shape[:2]
    w = h * iw / ih
    ax.imshow(image, extent=(x, x + w, y, y + h), aspect="equal", zorder=3)
    rounded(x - 0.06, y - 0.06, w + 0.12, h + 0.12, "#181818", face="none", lw=2.2, radius=0.22, z=4)
    ax.text(
        x + w / 2,
        y + h + 0.22,
        label,
        ha="center",
        va="bottom",
        color=C_BLUE,
        fontsize=12,
        fontweight="bold",
        fontfamily="DejaVu Sans",
    )
    if highlight:
        # highlight is expressed in normalized screenshot coordinates, origin at lower-left.
        hx, hy, hw, hh = highlight
        ax.add_patch(
            Rectangle(
                (x + hx * w, y + hy * h),
                hw * w,
                hh * h,
                fill=False,
                edgecolor=highlight_color,
                linewidth=2.4,
                linestyle="--" if highlight_color == C_RED else "-",
                zorder=6,
            )
        )
    return w


def labeled_box(x, y, w, h, label, text, edge, face="white", fs=11, italic_lines=()):
    rounded(x, y, w, h, edge, face=face, lw=1.8, radius=0.14)
    ax.text(
        x + w / 2,
        y + h + 0.05,
        f"  {label}  ",
        ha="center",
        va="center",
        fontsize=11.2,
        color=edge,
        fontweight="bold",
        fontfamily="DejaVu Sans",
        bbox=dict(facecolor="white", edgecolor="none", pad=0.2),
        zorder=5,
    )
    lines = text.split("\n")
    line_h = h / (len(lines) + 1)
    for i, line in enumerate(lines):
        ax.text(
            x + 0.16,
            y + h - (i + 1) * line_h,
            line,
            ha="left",
            va="center",
            fontsize=fs,
            color=C_GREEN if i in italic_lines else C_TEXT,
            fontstyle="italic" if i in italic_lines else "normal",
            fontweight="bold" if i in italic_lines else "normal",
            fontfamily="DejaVu Serif",
            zorder=5,
        )


def code_box(x, y, w, h, title, lines, accents=None, edge=C_GREEN, face=C_LIGHT_GREEN):
    rounded(x, y, w, h, edge, face=face, lw=1.7, radius=0.18)
    ax.text(
        x + 0.18,
        y + h - 0.18,
        title,
        ha="left",
        va="top",
        fontsize=11.2,
        color=edge,
        fontweight="bold",
        fontfamily="DejaVu Sans",
    )
    accents = accents or {}
    start_y = y + h - 0.55
    line_h = (h - 0.72) / max(len(lines), 1)
    for i, line in enumerate(lines):
        color = accents.get(i, C_TEXT)
        ax.text(
            x + 0.18,
            start_y - i * line_h,
            line,
            ha="left",
            va="top",
            fontsize=9.2,
            color=color,
            fontweight="bold" if color != C_TEXT else "normal",
            fontfamily="DejaVu Sans Mono",
            zorder=5,
        )


ax.text(
    10,
    13.95,
    "How the Execution Ledger Changes Agent Behavior",
    ha="center",
    va="center",
    fontsize=21,
    fontweight="bold",
    color=C_TEXT,
    fontfamily="DejaVu Sans",
)

# ---------------------------------------------------------------------------
# Row A: reflection changes the next action.
# ---------------------------------------------------------------------------
ax.text(0.25, 13.45, "(a) Reflection records failure context and redirects the next action", fontsize=14, fontweight="bold", color=C_TEXT)
ax.plot([0.25, 19.75], [7.28, 7.28], color="#d9dde0", lw=1.4)

w1 = phone(
    IMAGES["wrong_sort"],
    0.35,
    7.78,
    5.1,
    "Mistaken state",
    highlight=(0.02, 0.12, 0.96, 0.14),
    highlight_color=C_RED,
)
labeled_box(
    2.85,
    10.35,
    2.0,
    1.55,
    "thought",
    "I selected the\nSubtasks row,\nnot the main\nsort control.",
    C_BLUE,
    fs=10.2,
)
labeled_box(2.85, 8.25, 2.0, 1.28, "action", "swipe(540,1376\n -> 540,2400)", C_ORANGE, fs=10.3)
ax.text(3.85, 7.92, "X", ha="center", va="center", fontsize=30, color=C_RED, fontweight="bold")
arrow(2.52, 10.1, 2.78, 10.1)

code_box(
    5.18,
    8.0,
    4.3,
    4.62,
    "Ledger delta: reflection appended",
    [
        "reflect_on_ledger({",
        '  current_subtask_id: "find-filter",',
        '  reason: "wrong sorting option;',
        '           pixel coordinates",',
        '  next_step: "click Sorting row;',
        '              choose By priority"',
        "})",
        "",
        "+ reflection find-filter",
        "+ next_action = click(Sorting)",
    ],
    accents={8: C_GREEN, 9: C_GREEN},
)
arrow(4.88, 9.55, 5.12, 9.55)

w2 = phone(
    IMAGES["sort_menu"],
    9.82,
    7.78,
    5.1,
    "Revised action",
    highlight=(0.02, 0.27, 0.96, 0.10),
)
labeled_box(12.35, 10.25, 2.0, 1.48, "thought", "The ledger points\nto the correct row\nand target option.", C_BLUE, fs=10.2)
labeled_box(12.35, 8.28, 2.0, 1.1, "action", "click(By priority)", C_ORANGE, fs=10.5)
ax.text(13.35, 7.93, "OK", ha="center", va="center", fontsize=15, color=C_GREEN, fontweight="bold")
arrow(9.52, 10.1, 9.76, 10.1, color=C_GREEN)

w3 = phone(
    IMAGES["priority_result"],
    14.72,
    7.78,
    5.1,
    "Recovered state",
    highlight=(0.03, 0.73, 0.94, 0.09),
)
labeled_box(
    17.25,
    9.42,
    2.4,
    2.0,
    "validation",
    "High-priority items\nare now grouped first.\nMarketing Campaign\nLaunch is due Wed.",
    C_BLUE,
    fs=9.8,
    italic_lines=(2, 3),
)
arrow(14.42, 10.1, 14.66, 10.1, color=C_GREEN)

# ---------------------------------------------------------------------------
# Row B: validation gate requires the validation subtask to be complete.
# ---------------------------------------------------------------------------
ax.text(0.25, 6.88, "(b) Validation rejects a claimed success until the validation subtask is closed", fontsize=14, fontweight="bold", color=C_TEXT)

wb1 = phone(IMAGES["maze_before"], 0.35, 0.72, 5.35, "Last action")
labeled_box(2.9, 3.4, 1.95, 1.18, "action", "click(Right)", C_ORANGE, fs=11)
arrow(2.62, 3.95, 2.84, 3.95)

wb2 = phone(
    IMAGES["maze_success"],
    5.15,
    0.72,
    5.35,
    "Visible task success",
    highlight=(0.01, 0.81, 0.55, 0.07),
)
arrow(4.9, 3.95, 5.08, 3.95, color=C_GREEN)

code_box(
    7.7,
    3.55,
    3.75,
    2.25,
    "Ledger before validation",
    [
        "[done] navigate-x",
        "[open] verify-navigate",
        "       (subtask_for_validate)",
        "",
        "validate_ledger(true)",
    ],
    accents={1: C_RED, 4: C_BLUE},
    edge=C_BLUE,
    face="#f5f8fd",
)
code_box(
    7.7,
    0.72,
    3.75,
    2.25,
    "Validation gate",
    [
        "Cannot validate complete:",
        "validation subtask still open",
        "verify-navigate",
        "",
        "task_completed = false",
    ],
    accents={0: C_RED, 1: C_RED, 2: C_RED, 4: C_RED},
    edge=C_RED,
    face="#fff4f2",
)
arrow(7.42, 3.95, 7.64, 3.95)
arrow(9.58, 3.45, 9.58, 3.05, color=C_RED)

code_box(
    12.05,
    3.55,
    3.45,
    2.25,
    "Complete validation subtask",
    [
        "update_ledger({",
        '  id: "verify-navigate",',
        '  kind: "complete_subtask"',
        "})",
        "",
        "+ [done] verify-navigate",
    ],
    accents={5: C_GREEN},
)
arrow(11.52, 2.1, 11.98, 4.55, color=C_GREEN)

code_box(
    16.05,
    3.55,
    3.45,
    2.25,
    "Validate again",
    [
        "validate_ledger({",
        "  task_completed: true",
        "})",
        "",
        "LEDGER VALIDATION: COMPLETE",
        "finish() is now allowed",
    ],
    accents={4: C_GREEN, 5: C_GREEN},
)
arrow(15.58, 4.65, 15.98, 4.65, color=C_GREEN)

labeled_box(
    12.05,
    0.8,
    7.45,
    1.75,
    "core idea",
    "Visible UI evidence is necessary but not sufficient.\nThe ledger enforces explicit completion of validation subtasks\nbefore the agent can finish.",
    C_BLUE,
    fs=11.2,
    italic_lines=(1, 2),
)

plt.savefig(OUT.with_suffix(".png"), dpi=220, bbox_inches="tight", facecolor="white")
plt.savefig(OUT.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")
print(f"Saved {OUT.with_suffix('.png')}")
print(f"Saved {OUT.with_suffix('.pdf')}")
