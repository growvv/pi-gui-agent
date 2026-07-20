#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

base_image="${MOBILEWORLD_BASE_IMAGE:-ghcr.io/tongyi-mai/mobile_world:latest}"
mirror_image="${MOBILEWORLD_MIRROR_IMAGE:-ghcr.nju.edu.cn/tongyi-mai/mobile_world:latest}"
target_image="${MOBILEWORLD_AGENT_IMAGE:-pi-gui-agent/mobileworld:latest}"
source_fingerprint="$(./scripts/mobileworld-image-fingerprint.sh)"

if docker image inspect "${base_image}" >/dev/null 2>&1; then
  :
elif docker image inspect "${mirror_image}" >/dev/null 2>&1; then
  base_image="${mirror_image}"
else
  if ! docker pull "${base_image}"; then
    echo "Official registry unavailable; using mirror ${mirror_image}" >&2
    docker pull "${mirror_image}"
    base_image="${mirror_image}"
  fi
fi
args=(
  -f docker/mobileworld/Dockerfile.pi-gui
  --build-arg "MOBILEWORLD_IMAGE=${base_image}"
)
if [[ -n "${HTTP_PROXY:-}" ]]; then args+=(--build-arg "HTTP_PROXY=${HTTP_PROXY}"); fi
if [[ -n "${HTTPS_PROXY:-}" ]]; then args+=(--build-arg "HTTPS_PROXY=${HTTPS_PROXY}"); fi
if [[ -n "${HTTP_PROXY:-}" ]]; then args+=(--build-arg "http_proxy=${HTTP_PROXY}"); fi
if [[ -n "${HTTPS_PROXY:-}" ]]; then args+=(--build-arg "https_proxy=${HTTPS_PROXY}"); fi
if [[ -n "${NODE_MIRROR_URL:-}" ]]; then
  args+=(--build-arg "NODE_MIRROR_URL=${NODE_MIRROR_URL}")
fi
if [[ -n "${NPM_REGISTRY:-}" ]]; then args+=(--build-arg "NPM_REGISTRY=${NPM_REGISTRY}"); fi

docker build "${args[@]}" \
  --label "org.pi-gui-agent.mobileworld.source=${source_fingerprint}" \
  -t "${target_image}" .
