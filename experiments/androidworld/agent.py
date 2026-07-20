"""Shared process adapter between AndroidWorld and command-line agents."""

from __future__ import annotations

import math
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import time
from typing import Any

from android_world.agents import base_agent
from android_world.env import interface


class AndroidWorldCodingAgent(base_agent.EnvironmentInteractingAgent):
  """Runs one complete coding-agent session and lets AndroidWorld score it."""

  agent_id = 'coding-agent'
  trusts_process_exit = True

  def __init__(
      self, env: interface.AsyncEnv, workspace_dir: str | os.PathLike[str],
      adb_path: str = 'adb', serial: str = 'emulator-5554',
      timeout_seconds: int = 900, thinking: str = 'medium',
      settle_ms: int = 1500,
      session_dir: str | None = None, action_budget_multiplier: float = 2.0,
      min_actions: int = 30, name: str | None = None,
      server_url: str | None = None, enable_ledger_tool: bool = False,
      disable_ledger_tool: bool = False,
      **_: Any,
  ) -> None:
    super().__init__(env, name=name or self.agent_id, transition_pause=None)
    self.workspace_dir = Path(workspace_dir).resolve()
    self.adb_path = adb_path
    self.serial = serial
    self.timeout_seconds = timeout_seconds
    self.thinking = thinking
    self.settle_ms = settle_ms
    self.session_dir = session_dir
    self.action_budget_multiplier = action_budget_multiplier
    self.min_actions = min_actions
    self.server_url = server_url
    self.enable_ledger_tool = enable_ledger_tool
    self.disable_ledger_tool = disable_ledger_tool
    self._current_goal: str | None = None
    self._ran = False

  def reset(self, go_home: bool = False) -> None:
    super().reset(go_home)
    self._ran = False

  def step(self, goal: str) -> base_agent.AgentInteractionResult:
    if self._ran:
      return base_agent.AgentInteractionResult(
          done=False,
          data={'goal': goal, 'error': 'Agent was already run for this episode.'},
      )
    self._ran = True
    self._current_goal = goal
    self.validate()
    android_world_steps = self._max_steps if self._max_steps is not None else 30
    max_actions = max(
        self.min_actions,
        math.ceil(android_world_steps * self.action_budget_multiplier),
    )
    with tempfile.TemporaryDirectory() as temp_dir:
      # The runner stays root while the coding-agent process is demoted.
      os.chmod(temp_dir, 0o777)
      result_file = Path(temp_dir, 'result.json')
      self._prepare_agent_directories()
      command = self._as_agent_user(
          self.build_command(self._prompt(goal), max_actions, result_file)
      )
      started = time.monotonic()
      try:
        result = self._run(command, started)
        agent_result = self.read_result(result_file)
        self.archive_run(goal, result)
        answer = agent_result.get('answer')
        if isinstance(answer, str):
          self.env.interaction_cache = answer
        data: dict[str, Any] = {
            'agent': self.agent_id, 'goal': goal, 'command': command,
            'returncode': result.returncode,
            'agent_finished': result.returncode == 0 and (
                self.trusts_process_exit or agent_result.get('finished') is True
            ),
            'stdout': result.stdout[-20_000:],
            'stderr': result.stderr[-10_000:],
            'duration_seconds': time.monotonic() - started,
            'max_actions': max_actions,
            'attempts': [attempt_data(result, agent_result)],
        }
      except subprocess.TimeoutExpired as error:
        self.archive_timeout(goal, error)
        data = self.timeout_data(goal, command, max_actions, started, error)
    return base_agent.AgentInteractionResult(done=True, data=data)

  def validate(self) -> None:
    pass

  def build_command(
      self, prompt: str, max_actions: int, result_file: Path,
  ) -> list[str]:
    raise NotImplementedError

  def mcp_args(self, max_actions: int) -> list[str]:
    entrypoint = os.environ.get('ANDROID_GUI_MCP_BIN') or str(
        self.workspace_dir / 'agents' / 'pi_gui' / 'dist' / 'mcp.js'
    )
    args = [entrypoint]
    if self.server_url:
      args.extend(['--server-url', self.server_url])
    else:
      args.extend(['--adb', self.adb_path, '--serial', self.serial])
    args.extend([
        '--settle-ms', str(self.settle_ms), '--max-actions', str(max_actions),
    ])
    if self.session_dir:
      args.extend([
          '--screenshot-dir', str(Path(self.session_dir) / 'mcp-screenshots'),
      ])
    return args

  def ledger_mcp_args(
      self, max_actions: int, original_task: str | None = None,
  ) -> list[str]:
    args = self.mcp_args(max_actions)
    args.extend([
        '--toolset', 'ledger',
        '--task', original_task or self._current_goal or 'Unknown task',
    ])
    if self.session_dir:
      args.extend([
          '--ledger-dir', str(Path(self.session_dir).parent / 'ledgers'),
      ])
    return args

  def mcp_servers(
      self, max_actions: int, original_task: str | None = None,
  ) -> dict[str, dict[str, Any]]:
    servers = {
        'android-gui': {'command': 'node', 'args': self.mcp_args(max_actions)},
    }
    if self.enable_ledger_tool:
      servers['ledger'] = {
          'command': 'node',
          'args': self.ledger_mcp_args(max_actions, original_task),
      }
    return servers

  def read_result(self, path: Path) -> dict[str, Any]:
    del path
    return {}

  def archive_run(
      self, goal: str, result: subprocess.CompletedProcess[str],
  ) -> None:
    del goal, result

  def archive_timeout(self, goal: str, error: subprocess.TimeoutExpired) -> None:
    del goal, error

  def timeout_data(
      self, goal: str, command: list[str], max_actions: int,
      started: float,
      error: subprocess.TimeoutExpired,
  ) -> dict[str, Any]:
    return {
        'agent': self.agent_id, 'goal': goal, 'command': command,
        'returncode': None, 'agent_finished': False,
        'stdout': decode_output(error.stdout)[-20_000:],
        'stderr': decode_output(error.stderr)[-10_000:],
        'duration_seconds': time.monotonic() - started,
        'max_actions': max_actions, 'attempts': [],
        'error': f'Timed out after {self.timeout_seconds} seconds.',
    }

  def _prompt(self, goal: str) -> str:
    transport = (
        'All environment observation and actions must go through the '
        'android-gui MCP tools. ADB access is intentionally unavailable in '
        'this FastAPI evaluation mode. '
        if self.server_url else
        f'Use ADB with the serial {self.serial!r} for suitable auxiliary work. '
    )
    ledger = (
        'Use the ledger MCP tools update_ledger, reflect_on_ledger, and '
        'validate_ledger to track progress, reflect when useful, and validate '
        'the final result before stopping. '
        if self.enable_ledger_tool else ''
    )
    return (
        'You are operating an isolated AndroidWorld emulator. Complete the '
        'task using the android-gui MCP tools: screenshot, tap, long_press, '
        'swipe, type_text, open_app, and back. Every GUI action returns the '
        'updated screenshot and visible UI text; use screenshot whenever you '
        'need to inspect the current UI without performing an action. '
        f'{transport}{ledger}Perform the requested actions and verify the '
        f'final state before stopping.\n\nTask: {goal}'
    )

  def _run(self, command: list[str], started: float) -> subprocess.CompletedProcess[str]:
    remaining = self.timeout_seconds - (time.monotonic() - started)
    if remaining <= 0:
      raise subprocess.TimeoutExpired(command, self.timeout_seconds)
    return subprocess.run(
        command, cwd=self.workspace_dir, env=self.process_environment(),
        capture_output=True, text=True, timeout=remaining, check=False,
    )

  def process_environment(self) -> dict[str, str]:
    return {**os.environ, 'NO_COLOR': '1'}

  def _prepare_agent_directories(self) -> None:
    """Create runtime directories before the coding agent is demoted."""
    if not self.session_dir:
      return
    # A caller may use a virtual output path in a local dry-run. The worker
    # mount is present in benchmark containers, where preparation is needed.
    if not Path(self.session_dir).parent.exists():
      return
    session_dir = Path(self.session_dir)
    screenshot_dir = session_dir / 'mcp-screenshots'
    directories = (session_dir, screenshot_dir, *self._additional_runtime_directories(session_dir))
    for directory in directories:
      directory.mkdir(parents=True, exist_ok=True)
    user = os.environ.get('ANDROIDWORLD_AGENT_USER')
    if user and os.geteuid() == 0:
      account = pwd.getpwnam(user)
      for directory in directories:
        os.chown(directory, account.pw_uid, account.pw_gid)

  def _additional_runtime_directories(
      self, session_dir: Path,
  ) -> tuple[Path, ...]:
    if self.enable_ledger_tool:
      return (session_dir.parent / 'ledgers',)
    return ()

  def _as_agent_user(self, command: list[str]) -> list[str]:
    user = os.environ.get('ANDROIDWORLD_AGENT_USER')
    if not user or os.geteuid() != 0:
      return command
    account = pwd.getpwnam(user)
    home = os.environ.get('ANDROIDWORLD_AGENT_HOME', f'/home/{user}')
    return [
        'setpriv', f'--reuid={account.pw_uid}', f'--regid={account.pw_gid}',
        '--init-groups', 'env', f'HOME={home}', f'USER={user}',
        f'LOGNAME={user}', f'PATH={os.environ.get("PATH", "")}', *command,
    ]


def attempt_data(
    result: subprocess.CompletedProcess[str], agent_result: dict[str, Any],
) -> dict[str, Any]:
  return {
      'attempt': 1, 'returncode': result.returncode,
      'actions': agent_result.get('actions', 0),
      'steps': agent_result.get('steps', 0),
      'aborted': agent_result.get('aborted', False),
      'abort_reason': agent_result.get('abortReason'),
      'finished': agent_result.get('finished') is True or result.returncode == 0,
      'ledger_path': agent_result.get('ledgerPath'),
      'stdout': result.stdout[-4_000:], 'stderr': result.stderr[-4_000:],
  }


def decode_output(value: str | bytes | None) -> str:
  if value is None:
    return ''
  return value.decode(errors='replace') if isinstance(value, bytes) else value
