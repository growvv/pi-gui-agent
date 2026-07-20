"""Typed TOML configuration for AndroidWorld experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path
import re
import tomli
from typing import Any, TypeVar


AGENT_NAMES = ('pi-gui', 'claude-code', 'codex', 'openclaw')


@dataclass(frozen=True)
class ExperimentSettings:
  name: str
  workers: int = 4
  output_root: str = 'benchmark-results'
  worker_start_interval_seconds: float = 0.0
  startup_cpu_max_percent: float | None = None
  startup_cpu_stable_samples: int = 3
  startup_cpu_timeout_seconds: float = 600.0


@dataclass(frozen=True)
class AgentSettings:
  name: str = 'pi-gui'
  provider: str | None = None
  model: str | None = None
  thinking: str = 'medium'
  learning: bool = False
  enable_ledger_tool: bool = False
  disable_ledger_tool: bool = False
  max_steps: int = 100
  openclaw_model: str = 'mimo-v2.5'


@dataclass(frozen=True)
class SuiteSettings:
  family: str = 'android_world'
  transport: str = 'direct'
  tasks: tuple[str, ...] = ()
  combinations: int = 1
  seed: int = 30
  fixed_task_seed: bool = False
  setup_mode: str = 'auto'
  timeout_seconds: int = 1800
  action_budget_multiplier: float = 2.0
  min_actions: int = 30
  max_model_tokens: int = 4096
  settle_ms: int = 1500


@dataclass(frozen=True)
class ContainerSettings:
  image: str | None = None
  name_prefix: str = 'pi-gui-androidworld'
  env_file: str | None = '.env'
  proxy_url: str | None = None
  download_cache_dir: str | None = '~/.cache/pi-gui-agent/androidworld'
  keep_containers: bool = False
  forward_env: tuple[str, ...] = ()
  agent_config_dir: str | None = None


@dataclass(frozen=True)
class AndroidWorldConfig:
  experiment: ExperimentSettings
  agent: AgentSettings = field(default_factory=AgentSettings)
  suite: SuiteSettings = field(default_factory=SuiteSettings)
  container: ContainerSettings = field(default_factory=ContainerSettings)
  source: Path = field(default=Path(), compare=False)

  @property
  def image(self) -> str:
    return self.container.image or f'pi-gui-agent/{self.agent.name}:latest'

  def worker_payload(self, tasks: list[str], output_dir: str = '/output') -> dict[str, Any]:
    """Return the small, self-contained config consumed inside one worker."""
    return {
        'agent': asdict(self.agent),
        'suite': {**asdict(self.suite), 'tasks': tasks},
        'runtime': {
            'workspace_dir': '/workspace/pi-gui-agent',
            'adb_path': 'adb',
            'console_port': 5554,
            'grpc_port': 8554,
            'checkpoint_dir': f'{output_dir}/checkpoints',
            'session_dir': f'{output_dir}/runs',
            'server_url': 'http://127.0.0.1:5000',
        },
    }


@dataclass(frozen=True)
class WorkerSettings:
  workspace_dir: str
  adb_path: str
  console_port: int
  grpc_port: int
  checkpoint_dir: str
  session_dir: str
  server_url: str = 'http://127.0.0.1:5000'


@dataclass(frozen=True)
class WorkerConfig:
  agent: AgentSettings
  suite: SuiteSettings
  runtime: WorkerSettings


T = TypeVar('T')


def load_config(path_value: str | Path) -> AndroidWorldConfig:
  path = Path(path_value).expanduser().resolve()
  raw = _load_toml(path, set())
  unknown = set(raw) - {'experiment', 'agent', 'suite', 'container'}
  if unknown:
    raise ValueError(f'Unknown top-level config keys: {sorted(unknown)}')
  config = AndroidWorldConfig(
      experiment=_dataclass(ExperimentSettings, raw.get('experiment', {})),
      agent=_dataclass(AgentSettings, raw.get('agent', {})),
      suite=_dataclass(SuiteSettings, raw.get('suite', {})),
      container=_dataclass(ContainerSettings, raw.get('container', {})),
      source=path,
  )
  _validate(config)
  return config


def load_worker_config(path_value: str | Path) -> WorkerConfig:
  path = Path(path_value).resolve()
  raw = json.loads(path.read_text(encoding='utf8'))
  if not isinstance(raw, dict) or set(raw) != {'agent', 'suite', 'runtime'}:
    raise ValueError('Worker config must contain agent, suite, and runtime sections')
  config = WorkerConfig(
      agent=_dataclass(AgentSettings, raw['agent']),
      suite=_dataclass(SuiteSettings, raw['suite']),
      runtime=_dataclass(WorkerSettings, raw['runtime']),
  )
  _validate_agent(config.agent)
  _validate_suite(config.suite)
  _validate_transport(config.agent, config.suite)
  return config


def _load_toml(path: Path, loading: set[Path]) -> dict[str, Any]:
  if path in loading:
    raise ValueError(f'Circular config inheritance at {path}')
  if not path.is_file():
    raise FileNotFoundError(f'Config does not exist: {path}')
  loading.add(path)
  with path.open('rb') as stream:
    raw = tomli.load(stream)
  parent_value = raw.pop('extends', None)
  if parent_value is None:
    merged: dict[str, Any] = {}
  elif isinstance(parent_value, str):
    merged = _load_toml((path.parent / parent_value).resolve(), loading)
  else:
    raise ValueError(f'extends must be a string in {path}')
  loading.remove(path)
  return _deep_merge(merged, raw)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
  merged = dict(base)
  for key, value in override.items():
    if isinstance(value, dict) and isinstance(merged.get(key), dict):
      merged[key] = _deep_merge(merged[key], value)
    else:
      merged[key] = value
  return merged


def _dataclass(cls: type[T], values: Any) -> T:
  if not isinstance(values, dict):
    raise ValueError(f'{cls.__name__} must be a table')
  allowed = {item.name for item in fields(cls)}
  unknown = set(values) - allowed
  if unknown:
    raise ValueError(f'Unknown {cls.__name__} keys: {sorted(unknown)}')
  normalized = dict(values)
  for name in ('tasks', 'forward_env'):
    if name in normalized:
      if not isinstance(normalized[name], list) or not all(
          isinstance(value, str) for value in normalized[name]
      ):
        raise ValueError(f'{name} must be an array of strings')
      normalized[name] = tuple(normalized[name])
  return cls(**normalized)


def _validate(config: AndroidWorldConfig) -> None:
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', config.experiment.name):
    raise ValueError('experiment.name must be a filesystem-safe name')
  if config.experiment.workers < 1:
    raise ValueError('experiment.workers must be positive')
  experiment = config.experiment
  if experiment.worker_start_interval_seconds < 0:
    raise ValueError('experiment.worker_start_interval_seconds must not be negative')
  if (
      experiment.startup_cpu_max_percent is not None and
      not 0 < experiment.startup_cpu_max_percent <= 100
  ):
    raise ValueError('experiment.startup_cpu_max_percent must be in (0, 100]')
  if experiment.startup_cpu_stable_samples < 1:
    raise ValueError('experiment.startup_cpu_stable_samples must be positive')
  if experiment.startup_cpu_timeout_seconds <= 0:
    raise ValueError('experiment.startup_cpu_timeout_seconds must be positive')
  output_root = Path(config.experiment.output_root)
  if output_root.is_absolute() or '..' in output_root.parts:
    raise ValueError('experiment.output_root must stay inside the repository')
  _validate_agent(config.agent)
  _validate_suite(config.suite)
  _validate_transport(config.agent, config.suite)


def _validate_agent(agent: AgentSettings) -> None:
  if agent.name not in AGENT_NAMES:
    raise ValueError(f'agent.name must be one of {AGENT_NAMES}')
  if not isinstance(agent.enable_ledger_tool, bool):
    raise ValueError('agent.enable_ledger_tool must be a boolean')
  if not isinstance(agent.disable_ledger_tool, bool):
    raise ValueError('agent.disable_ledger_tool must be a boolean')
  if bool(agent.provider) != bool(agent.model):
    raise ValueError('agent.provider and agent.model must be supplied together')
  if agent.max_steps <= 0:
    raise ValueError('agent.max_steps must be positive')


def _validate_suite(suite: SuiteSettings) -> None:
  if suite.transport not in ('direct', 'fastapi'):
    raise ValueError('suite.transport must be direct or fastapi')
  if suite.setup_mode not in ('auto', 'always', 'never'):
    raise ValueError('suite.setup_mode must be auto, always, or never')
  positive = {
      'combinations': suite.combinations,
      'timeout_seconds': suite.timeout_seconds,
      'action_budget_multiplier': suite.action_budget_multiplier,
      'min_actions': suite.min_actions,
      'max_model_tokens': suite.max_model_tokens,
  }
  invalid = [name for name, value in positive.items() if value <= 0]
  if invalid:
    raise ValueError(f'Suite values must be positive: {invalid}')
  if suite.settle_ms < 0:
    raise ValueError('suite.settle_ms must not be negative')


def _validate_transport(agent: AgentSettings, suite: SuiteSettings) -> None:
  if suite.transport != 'fastapi':
    return
  if agent.name != 'pi-gui':
    raise ValueError('suite.transport = fastapi currently supports agent.name = pi-gui')
  if suite.setup_mode != 'always':
    raise ValueError('suite.transport = fastapi requires suite.setup_mode = always')
  if suite.fixed_task_seed:
    raise ValueError('FastAPI suite reinitialization does not support fixed_task_seed')
