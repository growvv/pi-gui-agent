#!/usr/bin/env python3
"""Merge terminal AndroidWorld baseline runs into one task-level summary."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import pickle
from typing import Any


TERMINAL_STATUSES = {'completed', 'failed'}


def checkpoint_episodes(run_dir: Path) -> list[dict[str, Any]]:
  episodes = []
  for path in sorted(run_dir.glob('worker-*/checkpoints/*.pkl.gz')):
    try:
      with gzip.open(path, 'rb') as stream:
        values = pickle.load(stream)
    except (OSError, EOFError, pickle.UnpicklingError):
      continue
    if isinstance(values, list):
      episodes.extend(value for value in values if isinstance(value, dict))
  return episodes


def baseline_rows(root: Path) -> list[dict[str, Any]]:
  runs: dict[str, list[tuple[str, Path, dict[str, Any]]]] = {}
  for path in root.glob('androidworld-baseline-*/manifest.json'):
    manifest = json.loads(path.read_text(encoding='utf8'))
    if manifest.get('status') not in TERMINAL_STATUSES:
      continue
    agent = str(manifest.get('agent') or path.parent.name)
    started_at = str(manifest.get('started_at') or '')
    runs.setdefault(agent, []).append((started_at, path.parent, manifest))

  rows = []
  for agent, agent_runs in sorted(runs.items()):
    latest: dict[str, dict[str, Any]] = {}
    expected = 0
    sources = []
    for _, run_dir, manifest in sorted(agent_runs):
      expected = max(expected, int(manifest.get('expected_episodes') or 0))
      sources.append(run_dir.name)
      for episode in checkpoint_episodes(run_dir):
        task = episode.get('task_template')
        if isinstance(task, str) and task:
          latest[task] = episode

    completed = [e for e in latest.values() if e.get('exception_info') is None]
    successful = sum(float(e.get('is_successful') or 0) for e in completed)
    rows.append({
        'agent': agent,
        'sources': sources,
        'expected': expected,
        'recorded': len(latest),
        'completed': len(completed),
        'exceptions': len(latest) - len(completed),
        'successful': successful,
        'success_rate': successful / len(completed) if completed else None,
    })
  return rows


def render(rows: list[dict[str, Any]]) -> str:
  lines = [
      '# AndroidWorld Baseline Summary', '',
      '| Agent | Expected | Recorded | Completed | Exceptions | Successful | Success rate |',
      '| --- | ---: | ---: | ---: | ---: | ---: | ---: |',
  ]
  for row in rows:
    rate = row['success_rate']
    rate_text = f'{float(rate):.2%}' if rate is not None else 'n/a'
    lines.append(
        f"| `{row['agent']}` | {row['expected']} | {row['recorded']} | "
        f"{row['completed']} | {row['exceptions']} | "
        f"{row['successful']:g} | {rate_text} |"
    )
  if not rows:
    lines.append('| _none_ |  |  |  |  |  |  |')
  if rows:
    lines.extend(['', '## Sources', ''])
    for row in rows:
      lines.append(f"- `{row['agent']}`: " + ', '.join(
          f"`{source}`" for source in row['sources']))
  return '\n'.join(lines) + '\n'


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--root', type=Path, default=Path('benchmark-results'))
  parser.add_argument('--output', type=Path)
  args = parser.parse_args()
  output = render(baseline_rows(args.root))
  if args.output:
    args.output.write_text(output, encoding='utf8')
  print(output, end='')


if __name__ == '__main__':
  main()
