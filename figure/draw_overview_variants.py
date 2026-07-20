#!/usr/bin/env python3
"""Create four deterministic CodeDroid overview candidates.

Candidate 3 is the raster gpt-image-2 result generated separately; this script
creates the other four variants so typography and tool names remain editable.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figure"
BLUE, ORANGE, GREEN, RED = "#3f73c8", "#ef9d12", "#43a833", "#d83b32"
INK, MUTED = "#202020", "#657078"


def box(ax, x, y, w, h, text, color, face="white", fs=11, mono=False, lw=1.8):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=.06,rounding_size=.12", facecolor=face, edgecolor=color, linewidth=lw))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=INK, fontsize=fs, family="DejaVu Sans Mono" if mono else "DejaVu Serif", linespacing=1.3)


def title(ax, subtitle):
    ax.text(10, 8.65, "CodeDroid: Executable Task State Management", ha="center", va="center", fontsize=19, fontweight="bold", color=INK)
    ax.text(10, 8.25, subtitle, ha="center", va="center", fontsize=10.5, color=MUTED, style="italic")


def arr(ax, x1, y1, x2, y2, c=ORANGE, lw=2.1):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15, color=c, linewidth=lw))


def base():
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 20); ax.set_ylim(0, 9); ax.axis("off")
    return fig, ax


def candidate1():
    fig, ax = base(); title(ax, "Task progress as executable code for long-horizon hybrid GUI + CLI agents")
    box(ax, 7.5, 5.6, 5, 1.45, "ledger.sh\n\ntask  |  subtask  |  reflection\nvalidation  |  finish", GREEN, "#eaf5df", 12, True, 2.2)
    ax.text(10, 5.35, "PERSISTENT EXECUTABLE TASK STATE", ha="center", color=GREEN, fontsize=10, fontweight="bold")
    box(ax, .8, 5.55, 3.9, 1.25, "User task\n\"Complete a mobile workflow\"", BLUE, "#f5f8fd", 12)
    box(ax, 15.3, 5.55, 3.9, 1.25, "Hybrid coding agent\nobserve -> reason -> act", BLUE, "#f5f8fd", 12)
    arr(ax, 4.8, 6.15, 7.35, 6.15); arr(ax, 12.55, 6.15, 15.2, 6.15)
    box(ax, 1.0, 2.6, 4.1, 1.25, "GUI tools\ntap  swipe  type_text\nAndroid device", ORANGE, "#fffaf0", 11)
    box(ax, 7.95, 2.6, 4.1, 1.25, "Ledger tools\nupdate_ledger\nreflect_on_ledger\nvalidate_ledger", GREEN, "#eaf5df", 10.5, True)
    box(ax, 14.9, 2.6, 4.1, 1.25, "CLI tools\nbash  adb\nfilesystem / terminal", ORANGE, "#fffaf0", 11)
    arr(ax, 8.0, 5.55, 3.1, 3.9, GREEN); arr(ax, 10, 5.55, 10, 3.95, GREEN); arr(ax, 12, 3.9, 12, 5.55, GREEN)
    arr(ax, 5.1, 3.2, 7.8, 3.2); arr(ax, 12.2, 3.2, 14.8, 3.2)
    box(ax, 6.3, .75, 7.4, .85, "Act in the environment; manage progress in executable code.", BLUE, "white", 14)
    ax.text(10, .25, "Explicit tools make decomposition, reflection, validation, and finish first-class actions.", ha="center", color=GREEN, fontsize=10)
    return fig


def candidate2():
    fig, ax = base(); title(ax, "From implicit conversation history to explicit, executable progress management")
    ax.plot([10,10],[.75,7.7], color="#b7bdc1", linestyle="--", linewidth=1.5)
    ax.text(5, 7.7, "WITHOUT LEDGER", ha="center", fontsize=12, fontweight="bold", color=RED)
    ax.text(15, 7.7, "WITH CODEDROID", ha="center", fontsize=12, fontweight="bold", color=GREEN)
    box(ax, 1, 5.2, 7.8, 1.3, "Long screenshot history\nold failures fade below the context window\nnext action is guessed from prose", RED, "#fff4f2", 11)
    for i, t in enumerate(["screenshot", "tap", "thought", "swipe", "error", "repeat"]):
        box(ax, 1.1 + (i%3)*2.5, 2.7 - (i//3)*1.25, 2.05, .8, t, RED if i in (4,5) else BLUE, "white", 10)
    box(ax, 11, 5.2, 7.8, 1.3, "ledger.sh\nstructured task / subtasks / reflection / validation\nnext action is read from persistent state", GREEN, "#eaf5df", 11, True)
    box(ax, 11.2, 3.0, 2.15, 1.0, "update_ledger", GREEN, "white", 10, True)
    box(ax, 13.9, 3.0, 2.15, 1.0, "reflect_on_ledger", GREEN, "white", 9.5, True)
    box(ax, 16.6, 3.0, 2.15, 1.0, "validate_ledger", GREEN, "white", 9.5, True)
    arr(ax, 12.25, 5.15, 12.25, 4.1, GREEN); arr(ax, 14.95, 5.15, 14.95, 4.1, GREEN); arr(ax, 17.65, 5.15, 17.65, 4.1, GREEN)
    arr(ax, 9.1, 4.5, 10.8, 4.5, BLUE, 2.5)
    ax.text(10, 4.8, "Progress as text history -> progress as executable code", ha="center", color=BLUE, fontsize=10, fontweight="bold")
    box(ax, 11.7, .85, 6.6, .9, "The screen changes. The executable ledger remembers.", BLUE, "white", 13)
    return fig


def candidate4():
    fig, ax = base(); title(ax, "One executable ledger persists across a long-horizon task")
    ax.text(1.2, 7.55, "TRANSIENT OBSERVATIONS", color=BLUE, fontsize=11, fontweight="bold")
    ax.text(1.2, 2.95, "PERSISTENT EXECUTABLE STATE", color=GREEN, fontsize=11, fontweight="bold")
    stages=[("1  Plan", "task + subtask"), ("2  Act", "complete_subtask"), ("3  Recover", "reflection + next_step"), ("4  Verify", "validation -> finish")]
    xs=[1.0,5.7,10.4,15.1]
    for x,(head,desc) in zip(xs,stages):
        box(ax,x,5.0,3.7,1.5,head+"\n\n"+desc, BLUE if head[0] in "12" else GREEN, "#f5f8fd" if head[0] in "12" else "#eaf5df", 11)
        if x<15: arr(ax,x+3.75,5.75,x+4.55,5.75)
        box(ax,x,1.1,3.7,1.25,"ledger.sh\n"+desc,GREEN,"#eaf5df",10,True)
        arr(ax,x+1.85,4.95,x+1.85,2.45,GREEN)
    ax.plot([1.2,18.6],[3.65,3.65], color=GREEN, lw=3)
    ax.text(10, .35, "Transient UI state is observed; task state is accumulated as code.", ha="center", color=GREEN, fontsize=12, fontweight="bold")
    return fig


def candidate5():
    fig, ax = base(); title(ax, "Three coordinated layers: agent reasoning, first-class tools, runtime state")
    ax.text(1, 7.45, "HYBRID CODING AGENT", color=BLUE, fontsize=12, fontweight="bold")
    box(ax, 6.4, 6.5, 7.2, 1.0, "Current screenshot + UI tree + ledger state\n-> choose the next tool", BLUE, "#f5f8fd", 12)
    ax.text(1, 4.75, "FIRST-CLASS TOOLS", color=GREEN, fontsize=12, fontweight="bold")
    box(ax, 1.0, 3.35, 4.1, 1.0, "GUI\ntap / swipe / type_text", ORANGE, "#fffaf0", 11)
    box(ax, 7.95, 3.35, 4.1, 1.0, "TASK STATE\nupdate / reflect / validate", GREEN, "#eaf5df", 10.5, True)
    box(ax, 14.9, 3.35, 4.1, 1.0, "CLI\nbash / adb / filesystem", ORANGE, "#fffaf0", 11)
    for x in (3.05,10,16.95): arr(ax, x, 6.5, x, 4.45, GREEN if x==10 else ORANGE)
    ax.text(1, 2.65, "RUNTIME STATE", color=BLUE, fontsize=12, fontweight="bold")
    box(ax, 1.0, 1.0, 4.1, 1.0, "Android device\nobservations", BLUE, "#f5f8fd", 11)
    box(ax, 7.95, 1.0, 4.1, 1.0, "ledger.sh\nexecutable task state", GREEN, "#eaf5df", 11, True)
    box(ax, 14.9, 1.0, 4.1, 1.0, "Terminal + filesystem\ncommand results", BLUE, "#f5f8fd", 11)
    for x in (3.05,10,16.95): arr(ax, x, 3.35, x, 2.05, GREEN if x==10 else ORANGE)
    arr(ax, 5.2,1.5,7.8,1.5,BLUE); arr(ax,12.2,1.5,14.7,1.5,BLUE)
    box(ax, 5.2, .15, 9.7, .55, "Progress management becomes an explicit, executable tool action.", BLUE, "white", 12)
    return fig


for i, maker in {1: candidate1, 2: candidate2, 4: candidate4, 5: candidate5}.items():
    fig = maker()
    fig.savefig(OUT / f"overview_candidate_{i}.png", dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT / f"overview_candidate_{i}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)
print("Saved deterministic overview candidates 1, 2, 4, 5.")
