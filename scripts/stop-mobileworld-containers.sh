#!/usr/bin/env bash
set -euo pipefail

prefix="${MOBILEWORLD_NAME_PREFIX:-pi_gui_mobileworld}"
mapfile -t containers < <(
  docker ps -a --filter "name=^${prefix}_" --format '{{.Names}}'
)
if (( ${#containers[@]} )); then
  docker rm -f "${containers[@]}"
fi
