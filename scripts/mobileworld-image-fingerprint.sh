#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

paths=(
  docker/mobileworld/Dockerfile.pi-gui \
  agents/pi_gui/package.json \
  agents/pi_gui/package-lock.json \
  agents/pi_gui/tsconfig.json \
  agents/pi_gui/src \
  agents/pi_gui/skills
  experiments/mobileworld
)

find "${paths[@]}" \
  -type f -not -path '*/__pycache__/*' -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  | sha256sum \
  | cut -d' ' -f1
