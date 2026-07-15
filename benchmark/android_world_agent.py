"""AndroidWorld adapter for the TypeScript pi GUI agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from android_world.agents import base_agent
from android_world.env import interface


class PiGuiAgent(base_agent.EnvironmentInteractingAgent):
  """Runs one complete pi tool loop and lets AndroidWorld score the result."""

  def __init__(
      self,
      env: interface.AsyncEnv,
      project_dir: str | os.PathLike[str],
      adb_path: str = 'adb',
      serial: str = 'emulator-5554',
      node_path: str = 'node',
      timeout_seconds: int = 900,
      learning: bool = False,
      name: str = 'pi_gui_agent',
  ) -> None:
    super().__init__(env, name=name, transition_pause=None)
    self.project_dir = Path(project_dir).resolve()
    self.adb_path = adb_path
    self.serial = serial
    self.node_path = node_path
    self.timeout_seconds = timeout_seconds
    self.learning = learning
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

    entrypoint = self.project_dir / 'dist' / 'cli.js'
    if not entrypoint.is_file():
      raise FileNotFoundError(
          f'{entrypoint} does not exist. Run `npm run build` before the benchmark.'
      )

    max_actions = self._max_steps if self._max_steps is not None else 30
    with tempfile.TemporaryDirectory() as temp_dir:
      result_file = Path(temp_dir, 'result.json')
      command = [
          self.node_path,
          str(entrypoint),
          '--adb',
          self.adb_path,
          '--serial',
          self.serial,
          '--max-actions',
          str(max_actions),
          '--result-file',
          str(result_file),
      ]
      if not self.learning:
        command.append('--no-learning')
      command.append(goal)

      started = time.monotonic()
      try:
        result = subprocess.run(
            command,
            cwd=self.project_dir,
            env={**os.environ, 'NO_COLOR': '1'},
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        agent_result = _read_agent_result(result_file)
        answer = agent_result.get('answer')
        if isinstance(answer, str):
          self.env.interaction_cache = answer
        agent_finished = result.returncode == 0 and agent_result.get('finished') is True
        data: dict[str, Any] = {
            'goal': goal,
            'command': command,
            'returncode': result.returncode,
            'agent_finished': agent_finished,
            'stdout': result.stdout[-20_000:],
            'stderr': result.stderr[-10_000:],
            'duration_seconds': time.monotonic() - started,
            'max_actions': max_actions,
        }
      except subprocess.TimeoutExpired as error:
        agent_finished = False
        data = {
            'goal': goal,
            'command': command,
            'returncode': None,
            'agent_finished': False,
            'stdout': _decode_timeout_output(error.stdout),
            'stderr': _decode_timeout_output(error.stderr),
            'duration_seconds': time.monotonic() - started,
            'max_actions': max_actions,
            'error': f'Timed out after {self.timeout_seconds} seconds.',
        }

    # AndroidWorld only scores the device state when the agent explicitly
    # finishes. Budget exhaustion, process errors, and timeouts remain failures.
    return base_agent.AgentInteractionResult(done=agent_finished, data=data)


def _read_agent_result(path: Path) -> dict[str, Any]:
  try:
    value = json.loads(path.read_text(encoding='utf8'))
  except (FileNotFoundError, json.JSONDecodeError):
    return {}
  return value if isinstance(value, dict) else {}


def _decode_timeout_output(value: str | bytes | None) -> str:
  if value is None:
    return ''
  if isinstance(value, bytes):
    return value.decode(errors='replace')[-20_000:]
  return value[-20_000:]
