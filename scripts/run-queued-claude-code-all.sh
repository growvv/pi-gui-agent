#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

poll_seconds="${LONG_TASK_POLL_SECONDS:-30}"
log_dir="${CLAUDE_QUEUE_LOG_DIR:-benchmark-results/queued-claude-code}"
mkdir -p "${log_dir}"
completion_file="${CLAUDE_QUEUE_COMPLETION_FILE:-${log_dir}/.completed}"
rm -f "${completion_file}"

long_tasks_running() {
  # Match only AndroidWorld runners using a long-* configuration. The grep
  # exclusion prevents this check from matching its own shell command.
  pgrep -af 'experiments\.androidworld\.parallel.*configs/androidworld/long[^ ]*\.toml' \
    | grep -v 'grep' >/dev/null && return 0

  # Also cover detached runners whose parent Python process is not visible.
  command -v docker >/dev/null 2>&1 || return 1
  docker ps --format '{{.Names}}' \
    | grep -E '(^|-)androidworld-long(-|$)' >/dev/null
}

echo "[$(date --iso-8601=seconds)] Waiting for existing long AndroidWorld tasks..."
while long_tasks_running; do
  echo "[$(date --iso-8601=seconds)] Long tasks still running; checking again in ${poll_seconds}s."
  sleep "${poll_seconds}"
done
echo "[$(date --iso-8601=seconds)] Long tasks finished; starting queued experiments."

run_config() {
  local config="$1"
  local stamp
  stamp="$(date +%Y%m%dT%H%M%S%z)"
  local log="${log_dir}/${stamp}-$(basename "${config%.toml}").log"
  echo "[$(date --iso-8601=seconds)] Starting ${config}; log: ${log}"
  python3 -m experiments.androidworld.parallel "${config}" 2>&1 | tee "${log}"
}

run_config configs/androidworld/all-claude-code.toml
run_config configs/androidworld/all-claude-code-ledger.toml

touch "${completion_file}"
echo "[$(date --iso-8601=seconds)] Both queued experiments completed."
