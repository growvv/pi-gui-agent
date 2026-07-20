#!/usr/bin/env python3
"""Merge AndroidWorld retries, preferring the newest successful task result."""
from pathlib import Path
import argparse
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.androidworld import report
from experiments.androidworld.registry_metadata import load_task_registry_metadata

GOAL_TASKS = {
    'Turn bluetooth off.': 'SystemBluetoothTurnOff',
    'Turn brightness to the max value.': 'SystemBrightnessMax',
}


def main():
  p = argparse.ArgumentParser()
  p.add_argument('--result-dir', action='append', required=True)
  p.add_argument('--output-dir', required=True)
  p.add_argument('--task-metadata', type=Path)
  p.add_argument('--registry-image')
  p.add_argument('--registry-family', default='android_world')
  args = p.parse_args()
  roots = [Path(x).resolve() for x in args.result_dir]
  output = Path(args.output_dir).resolve()
  output.mkdir(parents=True, exist_ok=True)
  official_metadata = {}
  if args.task_metadata:
    metadata_rows = json.loads(args.task_metadata.read_text(encoding='utf8'))
    official_metadata = {row['task_name']: row for row in metadata_rows}
  registry_image = args.registry_image or _registry_image(roots)
  registry_metadata = load_task_registry_metadata(
      registry_image, args.registry_family)
  (output / 'registry_metadata.json').write_text(json.dumps({
      'image': registry_image,
      'family': args.registry_family,
      'tasks': registry_metadata,
  }, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

  canonical_tasks = set()
  for root in roots:
    canonical_tasks.update(report.checkpoint_rows(root))
  candidates = {}
  for order, root in enumerate(roots):
    results = report.checkpoint_rows(root)
    goal_map = report.task_goal_map(root)
    sessions = report.load_sessions(root) or report.load_worker_logs(root)
    worker_tasks = {}
    try:
      manifest = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
      worker_tasks = {
          f"worker-{worker['worker_id']}": worker.get('tasks', [])
          for worker in manifest.get('workers', [])
      }
    except (OSError, json.JSONDecodeError, KeyError):
      pass
    worker_session_indexes = {}
    grouped = {}
    for s in sessions:
      worker = next((x for x in s['source'].parts if x.startswith('worker-')), '')
      index = worker_session_indexes.get(worker, 0)
      worker_session_indexes[worker] = index + 1
      fallback_tasks = worker_tasks.get(worker, [])
      fallback_name = fallback_tasks[index] if index < len(fallback_tasks) else None
      semantic_name = GOAL_TASKS.get(s['goal'])
      if s['goal'].startswith('Send a text message using Simple SMS Messenger to '):
        semantic_name = 'SimpleSmsSend'
      name = goal_map.get(
          (worker, s['goal']), semantic_name or fallback_name
          or report.task_name_for(s['goal'], results))
      entry = grouped.setdefault(name, {'goal': s['goal'], 'events': [], 'sources': []})
      entry['events'].extend(s['events'])
      entry['sources'].append(s['source'])
    for name, result in results.items():
      entry = grouped.get(name, {'goal': name, 'events': [], 'sources': []})
      candidates.setdefault(name, []).append((order, entry, result, root))
    for name, entry in grouped.items():
      if name in canonical_tasks and name not in results:
        candidates.setdefault(name, []).append((order, entry, None, root))

  tasks = []
  selections = []
  tasks_dir = output / 'tasks'
  if tasks_dir.exists():
    shutil.rmtree(tasks_dir)
  tasks_dir.mkdir()
  for name in sorted(candidates):
    choices = [c for c in candidates[name] if c[2] is not None]
    successful = [c for c in choices if c[2] and float(c[2].get('success') or 0) >= 1]
    non_exception = [c for c in choices if not c[2].get('exception')]
    chosen = (successful or non_exception or choices)[-1]
    _, entry, result, root = chosen
    app_names = registry_metadata.get(name, {}).get('app_names', [])
    slug, steps = report.render_task(
        name, entry['goal'], entry['events'], result, root, output,
        app_names=app_names)
    task_dir = tasks_dir / slug
    task_dir.mkdir()
    checkpoint = next(root.glob(f'worker-*/checkpoints/{name}_*.pkl.gz'), None)
    if checkpoint:
      shutil.copy2(checkpoint, task_dir / 'checkpoint.pkl.gz')
    for index, source in enumerate(entry.get('sources', []), 1):
      suffix = '' if len(entry['sources']) == 1 else f'-{index}'
      shutil.copy2(source, task_dir / f'trajectory{suffix}.jsonl')
      metadata = source.with_name('metadata.json')
      if metadata.is_file():
        shutil.copy2(metadata, task_dir / f'metadata{suffix}.json')
    screenshot_dir = task_dir / 'screenshots'
    copied_screenshots = set()
    for event in entry['events']:
      _, body, _ = report.event_info(event)
      event_root = Path(str(event.get('_report_root') or root))
      screenshot = report.screenshot_ref(event, body, event_root)
      if screenshot and screenshot.name not in copied_screenshots:
        screenshot_dir.mkdir(exist_ok=True)
        shutil.copy2(screenshot, screenshot_dir / screenshot.name)
        copied_screenshots.add(screenshot.name)
    official = official_metadata.get(name, {})
    tags = [tag for tag in official.get('tags', []) if tag] or ['untagged']
    task_metadata = {
        'task': name, 'goal': entry['goal'], 'source': root.name,
        'status': report.status_for(result, bool(entry['events'])),
        'reward': result.get('success'), 'exception': result.get('exception'),
        'runtime': result.get('runtime'), 'trajectory_files': len(entry.get('sources', [])),
        'screenshots': len(copied_screenshots),
        'difficulty': official.get('difficulty'),
        'optimal_steps': int(official['optimal_steps']) if official.get('optimal_steps') else None,
        'tags': tags,
        'app_names': app_names,
        'official_task_template': official.get('task_template'),
        'metadata_source': 'https://github.com/google-research/android_world/blob/main/android_world/task_metadata.json',
        'app_names_source': 'AndroidWorld registry task class app_names',
    }
    (task_dir / 'result.json').write_text(
        json.dumps(task_metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    tasks.append({'name': name, 'goal': entry['goal'], 'slug': slug, 'steps': steps,
                  'status': report.status_for(result, bool(entry['events'])),
                  'usage': report.token_usage(entry['events']),
                  'app_names': app_names})
    selections.append({
        'task': name, 'source': root.name,
        'status': report.status_for(result, bool(entry['events'])),
        'reward': result.get('success') if result else None,
    })
  metadata = {}
  try:
    metadata = json.loads((roots[-1] / 'manifest.json').read_text())
  except (OSError, json.JSONDecodeError):
    pass
  report.remove_stale_task_pages(output, {t['slug'] for t in tasks})
  report.render_index(tasks, output, metadata)
  (output / 'selection.json').write_text(json.dumps({
      'policy': 'newest success, otherwise newest non-exception result, otherwise newest exception',
      'sources': [root.name for root in roots],
      'registry_metadata': 'registry_metadata.json',
      'tasks': selections,
  }, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
  print(f'Generated {len(tasks)} task pages in {output}')


def _registry_image(roots: list[Path]) -> str:
  for root in reversed(roots):
    try:
      manifest = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError):
      continue
    image = manifest.get('container_image')
    if isinstance(image, str) and image:
      return image
  return 'pi-gui-agent/pi-gui:latest'


if __name__ == '__main__':
  main()
