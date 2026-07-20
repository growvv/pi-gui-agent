#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

poll_seconds="${CLAUDE_QUEUE_POLL_SECONDS:-30}"
log_dir="${CODEX_QUEUE_LOG_DIR:-benchmark-results/queued-codex}"
mkdir -p "${log_dir}"
claude_completion_file="${CLAUDE_QUEUE_COMPLETION_FILE:-benchmark-results/queued-claude-code/.completed}"

echo "[$(date --iso-8601=seconds)] Waiting for Claude Code full comparison tasks..."
while [[ ! -f "${claude_completion_file}" ]]; do
  echo "[$(date --iso-8601=seconds)] Claude Code tasks still running; checking again in ${poll_seconds}s."
  sleep "${poll_seconds}"
done
echo "[$(date --iso-8601=seconds)] Claude Code tasks finished; starting Codex full comparison."

stamp="$(date +%Y%m%dT%H%M%S%z)"
log="${log_dir}/${stamp}-all-codex.log"
python3 -m experiments.androidworld.parallel \
  configs/androidworld/all-codex.toml 2>&1 | tee "${log}"

echo "[$(date --iso-8601=seconds)] Codex full comparison completed."
