#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"
for ablation in no-learning medium-thinking; do
  python3 -m experiments.androidworld.parallel \
    "configs/androidworld/ablation-${ablation}.toml" "$@"
done
