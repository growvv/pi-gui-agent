"""AndroidWorld adapter for Claude Code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any

from experiments.androidworld.agent import AndroidWorldCodingAgent, decode_output


MCP_STARTUP_PROMPT = (
    'Before taking any task action, call WaitForMcpServers and wait until the '
    'android-gui server is ready. Do not use Bash, ADB, Read, or any other '
    'fallback while android-gui is pending. After it is ready, inspect the '
    'emulator with mcp__android-gui__screenshot and use the android-gui MCP '
    'tools for GUI actions. Never use adb screencap followed by Read; that '
    'duplicates the MCP screenshot path and can exceed provider image limits.'
)


class ClaudeCodeAgent(AndroidWorldCodingAgent):
  agent_id = 'claude-code'

  def build_command(self, prompt: str, max_actions: int, result_file: Path) -> list[str]:
    config = result_file.parent / 'claude-mcp.json'
    config.write_text(json.dumps({
        'mcpServers': {'android-gui': self._mcp_server(max_actions)},
    }), encoding='utf8')
    config.chmod(0o644)
    return self._claude_command(prompt, config)

  def _claude_command(self, prompt: str, config: Path) -> list[str]:
    return [os.environ.get('CLAUDE_BIN', 'claude'), '--print', '--output-format',
            'stream-json', '--verbose', '--dangerously-skip-permissions',
            '--append-system-prompt', MCP_STARTUP_PROMPT,
            '--mcp-config', str(config), '--strict-mcp-config', prompt]

  def _prompt(self, goal: str) -> str:
    return f'{MCP_STARTUP_PROMPT}\n\n{super()._prompt(goal)}'

  def _mcp_server(self, max_actions: int) -> dict[str, Any]:
    return {
        'command': 'node',
        'args': self.mcp_args(max_actions),
    }

  def archive_run(self, goal, result):
    session_id = claude_session_id(result.stdout)
    run_dir = self._run_dir(goal)
    if run_dir:
      self._archive(
          run_dir, goal, session_id, result.returncode,
          result.stdout, result.stderr,
      )

  def archive_timeout(self, goal, error):
    stdout = decode_output(error.stdout)
    session_id = claude_session_id(stdout)
    run_dir = self._run_dir(goal)
    if run_dir:
      self._archive(
          run_dir, goal, session_id, None, stdout,
          decode_output(error.stderr), timed_out=True,
      )

  def _run_dir(self, goal: str) -> Path | None:
    if not self.session_dir:
      return None
    slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', goal).strip('-')[:80] or 'task'
    return Path(self.session_dir) / 'claude-code' / f'{int(time.time() * 1000)}-{slug}'

  @staticmethod
  def _archive(run_dir, goal, session_id, returncode, stream, stderr,
               timed_out=False):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'stream.jsonl').write_text(stream, encoding='utf8')
    (run_dir / 'stderr.log').write_text(stderr, encoding='utf8')
    (run_dir / 'metadata.json').write_text(json.dumps({
        'goal': goal, 'session_id': session_id,
        'returncode': returncode, 'timed_out': timed_out,
    }, ensure_ascii=False, indent=2), encoding='utf8')


def claude_session_id(stream: str) -> str | None:
  for line in stream.splitlines():
    try:
      event = json.loads(line)
    except json.JSONDecodeError:
      continue
    value = event.get('session_id') if isinstance(event, dict) else None
    if isinstance(value, str) and value:
      return value
  return None
