"""AndroidWorld adapter for OpenClaw."""

import os
from pathlib import Path
from typing import Any

from experiments.androidworld.agent import AndroidWorldCodingAgent


class OpenClawAgent(AndroidWorldCodingAgent):
  agent_id = 'openclaw'

  def __init__(self, *args: Any, openclaw_model: str = 'mimo-v2.5', **kwargs: Any):
    super().__init__(*args, **kwargs)
    self.openclaw_model = openclaw_model

  def build_command(self, prompt: str, max_actions: int, result_file: Path) -> list[str]:
    del max_actions, result_file
    return [os.environ.get('OPENCLAW_BIN', 'openclaw'), 'agent', '--local',
            '--agent', 'main', '--json', '--thinking', self.thinking,
            '--model', self.openclaw_model, '--message', prompt]
