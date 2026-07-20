"""AndroidWorld adapter for Codex."""

import os
from pathlib import Path

from experiments.androidworld.agent import AndroidWorldCodingAgent


class CodexAgent(AndroidWorldCodingAgent):
  agent_id = 'codex'

  def build_command(self, prompt: str, max_actions: int, result_file: Path) -> list[str]:
    del result_file
    args = self.mcp_args(max_actions)
    command = [os.environ.get('CODEX_BIN', 'codex'), 'exec']
    base_url = os.environ.get('OPENAI_BASE_URL')
    if base_url:
      command.extend([
          '-c', 'model_provider="custom_openai"',
          '-c', 'model_providers.custom_openai.name="Custom OpenAI"',
          '-c', f'model_providers.custom_openai.base_url={_toml_string(base_url)}',
          '-c', 'model_providers.custom_openai.env_key="OPENAI_API_KEY"',
          '-c', 'model_providers.custom_openai.wire_api="responses"',
      ])
    command.extend([
        '-c', 'web_search="disabled"',
        '-c', 'shell_environment_policy.exclude=["OPENAI_API_KEY"]',
        '--disable', 'standalone_web_search',
        '-c', 'mcp_servers.android_gui.command="node"',
        '-c', f'mcp_servers.android_gui.args={_toml_array(args)}',
        '--model', 'mimo-v2.5',
        '--dangerously-bypass-approvals-and-sandbox',
        '--skip-git-repo-check', prompt,
    ])
    return command


def _toml_array(values: list[str]) -> str:
  import json
  return '[' + ','.join(json.dumps(value) for value in values) + ']'


def _toml_string(value: str) -> str:
  import json
  return json.dumps(value)
