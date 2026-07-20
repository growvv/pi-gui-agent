"""AndroidWorld adapter for pi-gui-agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent import AndroidWorldCodingAgent


class PiGuiAgent(AndroidWorldCodingAgent):
  agent_id = 'pi-gui'
  trusts_process_exit = False

  def __init__(
      self, *args: Any, node_path: str = 'node', learning: bool = False,
      provider: str | None = None, model: str | None = None,
      settle_ms: int = 1500, max_model_tokens: int = 4096,
      max_steps: int = 100,
      pi_gui_dir: str | None = None, **kwargs: Any,
  ) -> None:
    super().__init__(*args, **kwargs)
    self.pi_gui_dir = Path(pi_gui_dir or self.workspace_dir / 'agents' / 'pi_gui').resolve()
    self.workspace_dir = self.pi_gui_dir
    self.node_path = node_path
    self.learning = learning
    self.provider = provider
    self.model = model
    self.settle_ms = settle_ms
    self.max_model_tokens = max_model_tokens
    self.max_steps = max_steps

  def validate(self) -> None:
    entrypoint = self.pi_gui_dir / 'dist' / 'cli.js'
    if not entrypoint.is_file():
      raise FileNotFoundError(
          f'{entrypoint} does not exist. Run `npm run build` in {self.pi_gui_dir}.'
      )

  def _prompt(self, goal: str) -> str:
    """pi-gui owns its system prompt and must receive the raw benchmark goal."""
    return goal

  def _additional_runtime_directories(
      self, session_dir: Path,
  ) -> tuple[Path, ...]:
    if self.disable_ledger_tool:
      return ()
    return (session_dir.parent / 'ledgers',)

  def build_command(self, prompt: str, max_actions: int, result_file: Path) -> list[str]:
    command = [
        self.node_path, str(self.pi_gui_dir / 'dist' / 'cli.js'),
        '--max-actions', str(max_actions), '--max-model-tokens',
        str(self.max_model_tokens), '--max-steps', str(self.max_steps),
        '--thinking', self.thinking,
        '--settle-ms', str(self.settle_ms), '--result-file', str(result_file),
    ]
    if self.server_url:
      command.extend(['--server-url', self.server_url])
    else:
      command.extend(['--adb', self.adb_path, '--serial', self.serial])
    if self.provider and self.model:
      command.extend(['--provider', self.provider, '--model', self.model])
    if self.session_dir:
      command.extend(['--session-dir', self.session_dir])
      if not self.disable_ledger_tool:
        command.extend([
            '--ledger-dir', str(Path(self.session_dir).parent / 'ledgers'),
        ])
      command.extend([
          '--learning-root', str(Path(self.session_dir).parent / 'learning'),
      ])
    if not self.learning:
      command.append('--no-learning')
    if self.disable_ledger_tool:
      command.append('--disable-ledger-tool')
    command.append(prompt)
    return command

  def read_result(self, path: Path) -> dict[str, Any]:
    try:
      value = json.loads(path.read_text(encoding='utf8'))
    except (FileNotFoundError, json.JSONDecodeError):
      return {}
    return value if isinstance(value, dict) else {}
