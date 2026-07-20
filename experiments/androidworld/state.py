"""Worker-level progress and result state for AndroidWorld runs."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Iterable


class StateTrackingCheckpointer:
  """Forward checkpoint writes and notify the worker state writer."""

  def __init__(self, delegate: Any, on_save) -> None:
    self._delegate = delegate
    self._on_save = on_save
    self.episodes: list[dict[str, Any]] = []

  def save_episodes(self, episodes, checkpoint_name):
    result = self._delegate.save_episodes(episodes, checkpoint_name)
    self.episodes.extend(
        episode for episode in episodes if isinstance(episode, dict)
    )
    self._on_save(self.episodes)
    return result

  def __getattr__(self, name: str):
    return getattr(self._delegate, name)


def write_worker_state(
    path: Path,
    expected_tasks: Iterable[str],
    combinations: int,
    episodes: Iterable[dict[str, Any]],
    max_steps: int,
    worker_status: str,
) -> None:
  """Atomically write the current state of all tasks handled by a worker."""
  rows = {
      (task_name, instance_id): {
          'task_name': task_name,
          'instance_id': instance_id,
          'status': 'pending',
          'goal': None,
          'score': None,
          'result': None,
          'reached_max_steps': False,
          'exception': None,
      }
      for task_name in expected_tasks
      for instance_id in range(combinations)
  }
  for episode in episodes:
    if not isinstance(episode, dict):
      continue
    task_name = str(episode.get('task_template') or '')
    if not task_name:
      continue
    instance_id = _scalar(episode.get('instance_id'), 0)
    try:
      instance_id = int(instance_id)
    except (TypeError, ValueError):
      instance_id = 0
    score = _number(_scalar(episode.get('is_successful'), 0))
    exception = episode.get('exception_info')
    result = _agent_result(episode)
    attempts = result.get('attempts', [])
    reached_limit = any(
        isinstance(attempt, dict)
        and bool(attempt.get('aborted'))
        and (
            'step limit reached' in str(attempt.get('abort_reason', '')).lower()
            or _number(attempt.get('steps')) >= max_steps
        )
        for attempt in (attempts if isinstance(attempts, list) else [])
    )
    if exception:
      status = 'exception'
    elif score >= 1:
      status = 'success'
    elif score > 0:
      status = 'partial'
    else:
      status = 'failed'
    rows[(task_name, instance_id)] = {
        'task_name': task_name,
        'instance_id': instance_id,
        'status': status,
        'goal': _scalar(episode.get('goal')),
        'score': score,
        'result': _result_summary(result),
        'reached_max_steps': reached_limit,
        'exception': exception,
    }
  value = {
      'updated_at': dt.datetime.now().astimezone().isoformat(),
      'status': worker_status,
      'max_steps': max_steps,
      'tasks': list(rows.values()),
  }
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8',
    )
    temporary.replace(path)
  except OSError:
    # State is observability only and must not make an AndroidWorld episode fail.
    return


def _agent_result(episode: dict[str, Any]) -> dict[str, Any]:
  data = episode.get('episode_data')
  if not isinstance(data, dict):
    return {}
  result = _scalar(data.get('agent_result'))
  if not isinstance(result, dict):
    result = {
        key: value for key, value in data.items()
        if key in {
            'agent_finished', 'returncode', 'duration_seconds', 'actions',
            'steps', 'aborted', 'abort_reason', 'finished', 'answer',
            'ledger_path', 'attempts',
        }
    }
  # AndroidWorld wraps each agent-result field in a one-element list when
  # serializing episode data; normalize that shape for state consumers.
  normalized = {key: _scalar(value) for key, value in result.items()}
  attempts = normalized.get('attempts')
  if isinstance(attempts, dict):
    normalized['attempts'] = [attempts]
  return normalized


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
  summary: dict[str, Any] = {}
  for key in ('agent_finished', 'returncode', 'duration_seconds', 'actions', 'steps',
              'aborted', 'abort_reason', 'finished', 'answer', 'ledger_path'):
    if key in result:
      summary[key] = result[key]
  attempts = result.get('attempts')
  if isinstance(attempts, list):
    summary['attempts'] = [
        {
            key: attempt[key]
            for key in ('attempt', 'returncode', 'actions', 'steps', 'aborted',
                        'abort_reason', 'finished')
            if key in attempt
        }
        for attempt in attempts if isinstance(attempt, dict)
    ]
  return summary


def _scalar(value: Any, default: Any = None) -> Any:
  if isinstance(value, list):
    return value[0] if value else default
  return value if value is not None else default


def _number(value: Any) -> float:
  try:
    number = float(value or 0)
    return number if math.isfinite(number) else 0.0
  except (TypeError, ValueError):
    return 0.0
