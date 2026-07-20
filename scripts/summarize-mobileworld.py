#!/usr/bin/env python3
"""Generate a JSON summary for a MobileWorld result directory."""
import argparse
import json
import re
from pathlib import Path


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('result_dir', type=Path)
  parser.add_argument('--output', type=Path)
  args = parser.parse_args()
  trajectories = args.result_dir / 'trajectories'
  tasks = []
  for task_dir in sorted(p for p in trajectories.iterdir() if p.is_dir()):
    result_file = task_dir / 'result.txt'
    score = None
    reason = None
    if result_file.is_file():
      text = result_file.read_text(encoding='utf8', errors='replace').strip()
      match = re.search(r'^score:\s*([-+0-9.eE]+)', text, re.MULTILINE)
      if match:
        score = float(match.group(1))
      reason_match = re.search(r'^reason:\s*(.*)', text, re.MULTILINE | re.DOTALL)
      if reason_match:
        reason = reason_match.group(1).strip()
    status = 'missing' if score is None else ('success' if score >= 1 else 'failed')
    tasks.append({
        'task': task_dir.name,
        'score': score,
        'status': status,
        'reason': reason,
    })

  evaluated = [task for task in tasks if task['score'] is not None]
  successful = sum(task['status'] == 'success' for task in tasks)
  summary = {
      'result_dir': args.result_dir.name,
      'total_tasks': len(tasks),
      'evaluated_tasks': len(evaluated),
      'successful_tasks': successful,
      'failed_tasks': sum(task['status'] == 'failed' for task in tasks),
      'missing_tasks': sum(task['status'] == 'missing' for task in tasks),
      'total_score': sum(task['score'] for task in evaluated),
      'success_rate_evaluated': successful / len(evaluated) if evaluated else None,
      'success_rate_all': successful / len(tasks) if tasks else None,
      'tasks': tasks,
  }
  output = args.output or args.result_dir / 'summary.json'
  output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
  print(f'Wrote {len(tasks)} tasks to {output}')


if __name__ == '__main__':
  main()
