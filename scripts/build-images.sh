#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

docker build -f docker/androidworld/Dockerfile.base \
  --build-arg "AGENT_UID=$(id -u)" \
  --build-arg "AGENT_GID=$(id -g)" \
  -t pi-gui-agent/androidworld-base:latest .
for agent in pi-gui claude-code codex openclaw; do
  docker build -f "docker/androidworld/Dockerfile.${agent}" \
    -t "pi-gui-agent/${agent}:latest" .
done
