#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

image="${MOBILEWORLD_AGENT_IMAGE:-pi-gui-agent/mobileworld:latest}"
config="configs/mobileworld/main.toml"
if (( $# )) && [[ "$1" != --* ]]; then
  config="$1"
  shift
fi
if [[ ! -f "${config}" ]]; then
  echo "MobileWorld config does not exist: ${config}" >&2
  exit 2
fi

# The source label keeps the baked agent/adapter current without invoking a
# large legacy Docker build on every run.
if [[ "${MOBILEWORLD_SKIP_BUILD:-0}" != "1" ]]; then
  expected="$(./scripts/mobileworld-image-fingerprint.sh)"
  actual="$(
    docker image inspect --format \
      '{{index .Config.Labels "org.pi-gui-agent.mobileworld.source"}}' \
      "${image}" 2>/dev/null || true
  )"
  if [[ "${actual}" != "${expected}" ]] \
      || ! docker image inspect "${image}" >/dev/null 2>&1; then
    MOBILEWORLD_AGENT_IMAGE="${image}" ./scripts/build-mobileworld-image.sh
  fi
elif ! docker image inspect "${image}" >/dev/null 2>&1; then
  echo "MobileWorld image is missing" >&2
  exit 2
fi

mkdir -p benchmark-results
mobileworld_root="${MOBILEWORLD_ROOT:-}"
if [[ -z "${mobileworld_root}" || ! -d "${mobileworld_root}/src/mobile_world" ]]; then
  cat >&2 <<'EOF'
MobileWorld host checkout is required.
Set MOBILEWORLD_ROOT to the official MobileWorld repository, for example:
  export MOBILEWORLD_ROOT=/path/to/mobile_world
The host runner now invokes `uv run` directly; it no longer starts a control
container or mounts the Docker socket into one.
EOF
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required on the host to run MobileWorld" >&2
  exit 2
fi

export MOBILEWORLD_ROOT="$(realpath "${mobileworld_root}")"
export MOBILEWORLD_AGENT_IMAGE="${image}"
export PI_GUI_OUTPUT_ROOT="${root}/benchmark-results"
export PYTHONDONTWRITEBYTECODE=1
if [[ -f "${root}/.env" ]]; then
  export PI_GUI_ENV_FILE="${root}/.env"
fi

exec uv run --project "${MOBILEWORLD_ROOT}" \
  python -m experiments.mobileworld.run "$(realpath "${config}")" "$@"
