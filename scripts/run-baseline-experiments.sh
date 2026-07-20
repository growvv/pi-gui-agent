#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"
for agent in claude-code codex openclaw; do
  python3 -m experiments.androidworld.parallel \
    "configs/androidworld/baseline-${agent}.toml" "$@"
done
python3 scripts/summarize-baselines.py \
  --output benchmark-results/androidworld-baseline-summary.md
