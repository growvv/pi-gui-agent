"""AndroidWorld adapter for OpenClaw."""

import json
import os
from pathlib import Path
import pwd
from typing import Any

from experiments.androidworld.agent import AndroidWorldCodingAgent


class OpenClawAgent(AndroidWorldCodingAgent):
  agent_id = 'openclaw'

  def __init__(self, *args: Any, openclaw_model: str = 'mimo-v2.5', **kwargs: Any):
    super().__init__(*args, **kwargs)
    self.openclaw_model = openclaw_model

  def build_command(self, prompt: str, max_actions: int, result_file: Path) -> list[str]:
    config = self._write_mcp_config(prompt, max_actions, result_file)
    return ['env', f'OPENCLAW_CONFIG_PATH={config}',
            os.environ.get('OPENCLAW_BIN', 'openclaw'), 'agent', '--local',
            '--agent', 'main', '--json', '--thinking', self.thinking,
            '--model', self.openclaw_model, '--message', prompt]

  def _write_mcp_config(
      self, prompt: str, max_actions: int, result_file: Path,
  ) -> Path:
    configured = os.environ.get('OPENCLAW_CONFIG_PATH')
    agent_home = os.environ.get('ANDROIDWORLD_AGENT_HOME')
    source = (
        Path(configured).expanduser() if configured else
        Path(agent_home, '.openclaw', 'openclaw.json') if agent_home else
        Path.home() / '.openclaw' / 'openclaw.json'
    )
    try:
      value = json.loads(source.read_text(encoding='utf8'))
    except FileNotFoundError:
      if agent_home or configured:
        raise
      value = {}
    if not isinstance(value, dict):
      raise ValueError(f'OpenClaw config must be a JSON object: {source}')
    mcp = value.get('mcp')
    mcp = dict(mcp) if isinstance(mcp, dict) else {}
    mcp['servers'] = self.mcp_servers(
        max_actions, self._current_goal or prompt,
    )
    value['mcp'] = mcp

    target = (
        source.with_name('androidworld-mcp.json')
        if source.is_file() else result_file.parent / 'openclaw-mcp.json'
    )
    target.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    target.chmod(0o600)
    user = os.environ.get('ANDROIDWORLD_AGENT_USER')
    if user and os.geteuid() == 0:
      account = pwd.getpwnam(user)
      os.chown(target, account.pw_uid, account.pw_gid)
    return target
