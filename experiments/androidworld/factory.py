"""AndroidWorld agent registry."""

from typing import Any

from baselines import ClaudeCodeAgent, CodexAgent, OpenClawAgent
from .pi_gui_agent import PiGuiAgent

AGENT_CLASSES = {
    'pi-gui': PiGuiAgent,
    'claude-code': ClaudeCodeAgent,
    'codex': CodexAgent,
    'openclaw': OpenClawAgent,
}


def create_agent(agent: str, env: Any, **kwargs: Any):
  try:
    agent_class = AGENT_CLASSES[agent]
  except KeyError as error:
    raise ValueError(f'Unknown AndroidWorld agent: {agent}') from error
  return agent_class(env, **kwargs)
