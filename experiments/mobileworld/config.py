"""Typed configuration for MobileWorld experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
import re
from typing import Any, TypeVar

try:
  import tomllib
except ModuleNotFoundError:  # Python 3.10 test environment
  import tomli as tomllib


@dataclass(frozen=True)
class ExperimentSettings:
  name: str
  workers: int = 2


@dataclass(frozen=True)
class AgentSettings:
  provider: str | None = None
  model: str | None = None
  thinking: str = 'medium'
  learning: bool = False
  timeout_seconds: int = 1800
  max_actions: int = 50
  max_model_tokens: int = 4096
  settle_ms: int = 1500


@dataclass(frozen=True)
class SuiteSettings:
  tasks: tuple[str, ...] = ()
  max_retries: int = 2
  step_wait_seconds: float = 1.0


@dataclass(frozen=True)
class MobileWorldSettings:
  image: str = 'pi-gui-agent/mobileworld:latest'
  name_prefix: str = 'pi_gui_mobileworld'
  launch_interval_seconds: int = 20
  startup_timeout_seconds: int = 900
  backend_start_port: int = 16800
  viewer_start_port: int = 17860
  vnc_start_port: int = 15800
  reuse_containers: bool = True
  keep_containers: bool = True
  proxy_url: str | None = None
  proxy_relay_port: int = 17892


@dataclass(frozen=True)
class MobileWorldConfig:
  experiment: ExperimentSettings
  agent: AgentSettings = field(default_factory=AgentSettings)
  suite: SuiteSettings = field(default_factory=SuiteSettings)
  mobileworld: MobileWorldSettings = field(default_factory=MobileWorldSettings)

  def public_config(self) -> dict[str, Any]:
    return {
        'experiment': asdict(self.experiment),
        'agent': asdict(self.agent),
        'suite': asdict(self.suite),
        'mobileworld': asdict(self.mobileworld),
    }


T = TypeVar('T')


def load_config(path_value: str | Path) -> MobileWorldConfig:
  path = Path(path_value).expanduser().resolve()
  with path.open('rb') as stream:
    raw = tomllib.load(stream)
  unknown = set(raw) - {'experiment', 'agent', 'suite', 'mobileworld'}
  if unknown:
    raise ValueError(f'Unknown top-level config keys: {sorted(unknown)}')
  config = MobileWorldConfig(
      experiment=_dataclass(ExperimentSettings, raw.get('experiment', {})),
      agent=_dataclass(AgentSettings, raw.get('agent', {})),
      suite=_dataclass(SuiteSettings, raw.get('suite', {})),
      mobileworld=_dataclass(MobileWorldSettings, raw.get('mobileworld', {})),
  )
  _validate(config)
  return config


def _dataclass(cls: type[T], values: Any) -> T:
  if not isinstance(values, dict):
    raise ValueError(f'{cls.__name__} must be a table')
  allowed = {item.name for item in fields(cls)}
  unknown = set(values) - allowed
  if unknown:
    raise ValueError(f'Unknown {cls.__name__} keys: {sorted(unknown)}')
  normalized = dict(values)
  if cls is SuiteSettings and 'tasks' in normalized:
    tasks = normalized['tasks']
    if not isinstance(tasks, list) or not all(isinstance(value, str) for value in tasks):
      raise ValueError('tasks must be an array of strings')
    normalized['tasks'] = tuple(tasks)
  return cls(**normalized)


def _validate(config: MobileWorldConfig) -> None:
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', config.experiment.name):
    raise ValueError('experiment.name must be a filesystem-safe name')
  if config.experiment.workers < 1:
    raise ValueError('experiment.workers must be positive')
  if bool(config.agent.provider) != bool(config.agent.model):
    raise ValueError('agent.provider and agent.model must be supplied together')
  positive = {
      'timeout_seconds': config.agent.timeout_seconds,
      'max_actions': config.agent.max_actions,
      'max_model_tokens': config.agent.max_model_tokens,
      'step_wait_seconds': config.suite.step_wait_seconds,
  }
  invalid = [name for name, value in positive.items() if value <= 0]
  if invalid:
    raise ValueError(f'Values must be positive: {invalid}')
  if config.agent.settle_ms < 0 or config.suite.max_retries < 0:
    raise ValueError('settle_ms and max_retries must not be negative')
  if config.mobileworld.launch_interval_seconds < 0:
    raise ValueError('launch_interval_seconds must not be negative')
  ports = (
      config.mobileworld.backend_start_port,
      config.mobileworld.viewer_start_port,
      config.mobileworld.vnc_start_port,
      config.mobileworld.proxy_relay_port,
  )
  if any(port < 1 or port > 65535 for port in ports):
    raise ValueError('MobileWorld start ports must be between 1 and 65535')
