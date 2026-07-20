#!/usr/bin/env python3
"""Generate a self-contained AndroidWorld trajectory browser."""

from __future__ import annotations

import argparse
import gzip
import html
import json
import os
from pathlib import Path
import pickle
import re
from typing import Any


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--result-dir', required=True, action='append',
      help='result directory; repeat to merge retries in argument order',
  )
  parser.add_argument('--output-dir', required=True)
  return parser.parse_args()


def text_content(content: Any) -> str:
  if isinstance(content, str):
    return content
  if not isinstance(content, list):
    return ''
  parts = []
  for item in content:
    if not isinstance(item, dict):
      continue
    if item.get('type') in ('text', 'thinking'):
      parts.append(str(item.get('text') or item.get('thinking') or ''))
    elif item.get('type') == 'tool_result':
      parts.append(text_content(item.get('content')))
  return '\n'.join(part for part in parts if part)


def assistant_content(content: Any) -> str:
  body = text_content(content)
  calls = []
  for item in content if isinstance(content, list) else []:
    if not isinstance(item, dict) or item.get('type') not in ('toolCall', 'tool_use'):
      continue
    arguments = json.dumps(
        item.get('arguments', item.get('input', {})), ensure_ascii=False)
    calls.append(f"{item.get('name', 'tool')}({arguments})")
  parts = [part for part in (body, '\n'.join(calls)) if part]
  return '\n\n'.join(parts)


def task_goal(events: list[dict[str, Any]], metadata: Path) -> str:
  for event in events:
    message = event.get('message', {})
    if message.get('role') != 'user':
      continue
    value = text_content(message.get('content'))
    prompt = value.rsplit('\n\nThe attached image', 1)[0]
    task_markers = list(re.finditer(r'(?:^|\n\n)Task:\s*', prompt))
    if task_markers:
      return normalize_goal(prompt[task_markers[-1].end():])
    match = re.search(r'\n\n(.+?)\n\nThe attached image', value, re.S)
    if match:
      return normalize_goal(match.group(1))
  try:
    value = str(json.loads(metadata.read_text(encoding='utf8')).get('goal', ''))
    return normalize_goal(value)
  except (FileNotFoundError, json.JSONDecodeError):
    return ''


def normalize_goal(value: str) -> str:
  return re.sub(r'^Task:\s*', '', value.strip(), count=1)


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
  """Drop inline base64 images while retaining text and screenshot metadata."""
  message = event.get('message')
  if not isinstance(message, dict):
    return event
  content = message.get('content')
  if isinstance(content, list):
    message = dict(message)
    message['content'] = compact_content(content)
    event = dict(event)
    event['message'] = message
  return event


def compact_content(content: list[Any]) -> list[Any]:
  compacted = []
  for item in content:
    if not isinstance(item, dict):
      compacted.append(item)
      continue
    if item.get('type') == 'image':
      continue
    item = dict(item)
    if isinstance(item.get('content'), list):
      item['content'] = compact_content(item['content'])
    compacted.append(item)
  return compacted


def load_sessions(root: Path) -> list[dict[str, Any]]:
  sessions = []
  for source in sorted(root.glob('worker-*/runs/**/*.jsonl')):
    events = []
    for line in source.read_text(errors='replace').splitlines():
      try:
        event = compact_event(json.loads(line))
        event['_report_root'] = str(root)
        events.append(event)
      except json.JSONDecodeError:
        continue
    goal = task_goal(events, source.with_name('metadata.json'))
    if goal:
      sessions.append({'goal': goal, 'events': events, 'source': source})
  return sessions


def load_worker_logs(root: Path) -> list[dict[str, Any]]:
  """Create basic trajectories for backends that only emit worker logs."""
  sessions = []
  pattern = re.compile(r'Running task ([A-Za-z0-9_]+) with goal "(.*?)"')
  for source in sorted(root.glob('worker-*/worker.log')):
    current: dict[str, Any] | None = None
    for line in source.read_text(errors='replace').splitlines():
      match = pattern.search(line)
      if match:
        if current:
          sessions.append(current)
        current = {'goal': match.group(1), 'events': [], 'source': source}
      if current and line.strip():
        current['events'].append({
            'type': 'log', 'timestamp': '',
            'message': {'role': 'system', 'content': line},
            '_report_root': str(root),
        })
    if current:
      sessions.append(current)
  return sessions


def task_goal_map(root: Path) -> dict[tuple[str, str], str]:
  """Map the natural-language session goal back to its task template."""
  mapping = {}
  start_pattern = re.compile(r'Running task ([A-Za-z0-9_]+) with goal "(.*)')
  for source in root.glob('worker-*/worker.log'):
    worker = source.parent.name
    lines = source.read_text(errors='replace').splitlines()
    index = 0
    while index < len(lines):
      match = start_pattern.match(lines[index])
      if not match:
        index += 1
        continue
      task, first = match.groups()
      goal_lines = [first]
      while not goal_lines[-1].endswith('"') and index + 1 < len(lines):
        index += 1
        goal_lines.append(lines[index])
      if goal_lines[-1].endswith('"'):
        goal_lines[-1] = goal_lines[-1][:-1]
      mapping[(worker, '\n'.join(goal_lines).strip())] = task
      index += 1
  return mapping


def checkpoint_rows(root: Path) -> dict[str, dict[str, Any]]:
  """Read AndroidWorld's gzip-pickle checkpoints without importing the SDK."""
  rows: dict[str, dict[str, Any]] = {}
  for source in root.glob('worker-*/checkpoints/*.pkl.gz'):
    try:
      with gzip.open(source, 'rb') as checkpoint:
        episodes = pickle.load(checkpoint)
    except (OSError, pickle.UnpicklingError, EOFError):
      continue
    for episode in episodes if isinstance(episodes, list) else []:
      if not isinstance(episode, dict) or not episode.get('task_template'):
        continue
      rows[str(episode['task_template'])] = {
          'success': episode.get('is_successful'),
          'exception': episode.get('exception_info'),
          'runtime': episode.get('run_time'),
      }
  return rows


def event_info(event: dict[str, Any]) -> tuple[str, str, str]:
  typ = str(event.get('type', ''))
  message = event.get('message', {})
  role = message.get('role')
  if typ in ('message', 'assistant') and role == 'assistant':
    return 'Thinking & action', assistant_content(message.get('content')), 'thinking'
  content = message.get('content')
  has_tool_result = isinstance(content, list) and any(
      isinstance(item, dict) and item.get('type') == 'tool_result'
      for item in content)
  if (typ == 'message' and role == 'toolResult') or has_tool_result:
    return 'Action result', text_content(message.get('content')), 'action'
  if typ in ('message', 'user') and role == 'user':
    return 'Observation', text_content(message.get('content')), 'observation'
  if typ == 'log':
    return 'Worker log', str(message.get('content', '')), 'log'
  if typ == 'model_change':
    return 'Model', f"{event.get('provider', '')} / {event.get('modelId', '')}", 'meta'
  if typ == 'thinking_level_change':
    return 'Thinking level', str(event.get('thinkingLevel', '')), 'meta'
  if typ == 'compaction':
    return 'Context compaction', 'The session context was compacted.', 'meta'
  return typ.replace('_', ' ').title(), '', 'meta'


def screenshot_ref(event: dict[str, Any], body: str, root: Path) -> Path | None:
  candidates = []
  details = event.get('message', {}).get('details', {})
  if isinstance(details, dict):
    candidates.append(str(details.get('archivePath', '')))
  match = re.search(r'Screenshot archive:\s*([^\s]+)', body)
  if match:
    candidates.append(match.group(1))
  for candidate in candidates:
    name = Path(candidate).name
    if not name:
      continue
    for directory in ('screenshots', 'mcp-screenshots'):
      found = next(root.glob(f'worker-*/runs/{directory}/{name}'), None)
      if found:
        return found
  return None


def status_for(result: dict[str, Any] | None, has_events: bool) -> str:
  if result and result.get('exception'):
    return 'exception'
  if result and float(result.get('success') or 0) >= 1:
    return 'success'
  if result:
    if float(result.get('success') or 0) > 0:
      return 'partial'
    return 'failed'
  return 'unknown' if has_events else 'missing'


def task_name_for(goal: str, results: dict[str, dict[str, Any]]) -> str:
  first_line = goal.splitlines()[0].strip()
  if first_line in results:
    return first_line
  return next((name for name in results if name in goal), first_line[:120])


def fmt_time(timestamp: Any) -> str:
  value = str(timestamp or '')
  match = re.search(r'T(\d\d:\d\d:\d\d)', value)
  return match.group(1) if match else value


def token_usage(events: list[dict[str, Any]]) -> dict[str, int | float]:
  totals = {key: 0 for key in ('input', 'output', 'cacheRead', 'cacheWrite', 'reasoning')}
  aliases = {
      'input': ('input', 'input_tokens'),
      'output': ('output', 'output_tokens'),
      'cacheRead': ('cacheRead', 'cache_read_input_tokens'),
      'cacheWrite': ('cacheWrite', 'cache_creation_input_tokens'),
      'reasoning': ('reasoning', 'reasoning_tokens'),
  }
  calls = 0
  for event in events:
    usage = event.get('usage') or event.get('message', {}).get('usage')
    if not isinstance(usage, dict):
      continue
    calls += 1
    for key in totals:
      value = next((usage[name] for name in aliases[key] if name in usage), 0)
      if isinstance(value, (int, float)):
        totals[key] += int(value)
  prompt = totals['input'] + totals['cacheRead']
  totals['total'] = totals['input'] + totals['output'] + totals['cacheRead'] + totals['cacheWrite']
  totals['cacheRate'] = totals['cacheRead'] / prompt if prompt else 0
  totals['calls'] = calls
  return totals


def compact_number(value: int | float) -> str:
  number = float(value)
  if number >= 1_000_000:
    return f'{number / 1_000_000:.1f}M'
  if number >= 1_000:
    return f'{number / 1_000:.1f}K'
  return str(int(number))


def load_registry_app_names(paths: list[Path]) -> dict[str, list[str]]:
  app_names: dict[str, list[str]] = {}
  for path in paths:
    source = path / 'registry_metadata.json'
    try:
      value = json.loads(source.read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError):
      continue
    tasks = value.get('tasks', {}) if isinstance(value, dict) else {}
    if not isinstance(tasks, dict):
      continue
    for name, row in tasks.items():
      names = row.get('app_names', []) if isinstance(row, dict) else []
      if isinstance(name, str) and isinstance(names, list) and all(
          isinstance(app_name, str) for app_name in names
      ):
        app_names[name] = names
  return app_names


def render_task(name: str, goal: str, events: list[dict[str, Any]],
                result: dict[str, Any] | None, root: Path, output: Path,
                app_names: list[str] | tuple[str, ...] = ()) -> tuple[str, int]:
  slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', name).strip('-') or 'task'
  cards = []
  step = 0
  last_image = ''
  for event in events:
    label, body, kind = event_info(event)
    event_usage = token_usage([event])
    if not body:
      if not event_usage['calls']:
        continue
      label, body, kind = 'Model call', 'No textual content was returned.', 'meta'
    event_root = Path(str(event.get('_report_root') or root))
    image = screenshot_ref(event, body, event_root)
    if image:
      last_image = Path(os.path.relpath(image, output)).as_posix()
    is_step = kind == 'thinking'
    if is_step:
      step += 1
    step_number = f'Step {step}' if is_step else ''
    step_markup = (
        f'<span class="step-number">{step_number}</span>'
        if step_number else '')
    event_step = str(step) if is_step else ''
    safe_body = html.escape(body[:20000])
    usage_html = ''
    if event_usage['calls']:
      usage_html = (
          '<div class="event-usage">'
          f'<span>Input <b>{int(event_usage["input"]):,}</b></span>'
          f'<span>Cached <b>{int(event_usage["cacheRead"]):,}</b></span>'
          f'<span>Output <b>{int(event_usage["output"]):,}</b></span>'
          f'<span>Reasoning <b>{int(event_usage["reasoning"]):,}</b></span></div>')
    cards.append(
        f'<article class="event {kind}" data-step="{event_step}" data-image="{html.escape(last_image)}">'
        f'<div class="event-head"><span class="event-icon">{ICONS.get(kind, ICONS["meta"])}</span>'
        f'{step_markup}<strong>{html.escape(label)}</strong>'
        f'<time>{html.escape(fmt_time(event.get("timestamp")))}</time>'
        f'<button class="event-toggle" title="Expand event" aria-expanded="false">{ICONS["expand"]}</button></div>'
        f'<pre>{safe_body}</pre>{usage_html}</article>'
    )
  status = status_for(result, bool(events))
  runtime = result.get('runtime') if result else None
  runtime_text = f'{runtime:.1f}s' if isinstance(runtime, (int, float)) else '--'
  score = result.get('success') if result else None
  score_text = f'{float(score):g}' if isinstance(score, (int, float)) else '--'
  usage = token_usage(events)
  cache_rate = f'{float(usage["cacheRate"]):.1%}' if usage['calls'] else '--'
  apps_markup = ''
  if app_names:
    apps_markup = (
        '<p class="task-apps-detail"><span>Apps</span>'
        f'{html.escape(" · ".join(app_names))}</p>')
  initial_image = next((re.search(r'data-image="([^"]+)"', card).group(1)
                        for card in cards if 'data-image="../' in card), '')
  body = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)} · Trajectory</title>
  <style>{CSS}</style></head><body><nav><a class="brand" href="index.html">{ICONS['android']}<span>AndroidWorld</span></a><a class="back" href="index.html">{ICONS['back']} All tasks</a></nav>
  <main class="detail"><header class="task-header"><div><p class="eyebrow">Task trajectory</p><h1>{html.escape(name)}</h1><p class="goal">{html.escape(goal)}</p>{apps_markup}</div>
  <div class="summary"><span class="badge {status}">{status}</span><span><b>{score_text}</b> reward</span><span><b>{step}</b> steps</span><span><b>{runtime_text}</b> runtime</span></div></header>
  <section class="usage-strip"><div><span>Total tokens</span><b>{int(usage['total']):,}</b><small>{int(usage['calls'])} model calls</small></div><div><span>Input</span><b>{int(usage['input']):,}</b><small>uncached</small></div><div><span>Output</span><b>{int(usage['output']):,}</b><small>{int(usage['reasoning']):,} reasoning</small></div><div><span>Cache read</span><b>{int(usage['cacheRead']):,}</b><small>{int(usage['cacheWrite']):,} written</small></div><div><span>Cache rate</span><b>{cache_rate}</b><small>cached / prompt</small></div></section>
  <div class="trajectory"><section id="events" class="events">{''.join(cards) or '<div class="empty">No trajectory events were recorded.</div>'}</section>
  <aside class="phone-panel"><div class="phone"><div class="phone-top"></div><button id="imageButton" title="Open full-size screenshot"><img id="screen" src="{initial_image}" alt="Android screen at selected event"><span id="noScreen">No screenshot for this event</span></button></div><p id="shotLabel">Screen at selected event</p></aside></div></main>
  <dialog id="lightbox"><button id="close" title="Close">{ICONS['close']}</button><img id="fullScreen" alt="Full-size Android screenshot"></dialog>
  <script>{DETAIL_JS}</script></body></html>'''
  (output / f'{slug}.html').write_text(body, encoding='utf8')
  return slug, step


def render_index(tasks: list[dict[str, Any]], output: Path, metadata: dict[str, Any]) -> None:
  counts = {key: sum(task['status'] == key for task in tasks)
            for key in ('success', 'partial', 'failed', 'exception', 'unknown', 'missing')}
  cards = []
  aggregate = {key: sum(float(task['usage'][key]) for task in tasks)
               for key in ('input', 'output', 'cacheRead', 'cacheWrite', 'reasoning', 'total', 'calls')}
  prompt = aggregate['input'] + aggregate['cacheRead']
  aggregate_cache_rate = aggregate['cacheRead'] / prompt if prompt else 0
  app_counts: dict[str, int] = {}
  for task in tasks:
    for app_name in task.get('app_names', []):
      app_counts[app_name] = app_counts.get(app_name, 0) + 1
  for task in tasks:
    usage = task['usage']
    app_names = task.get('app_names', [])
    apps_text = ' · '.join(app_names)
    apps_markup = (
        f'<span class="task-apps"><b>Apps</b>{html.escape(apps_text)}</span>'
        if apps_text else '')
    cache_rate = f'{float(usage["cacheRate"]):.0%}' if usage['calls'] else '--'
    search_text = ' '.join((task['name'], task['goal'], *app_names)).lower()
    encoded_apps = html.escape(
        json.dumps(app_names, ensure_ascii=False), quote=True)
    cards.append(
        f'<a class="task-row" href="{task["slug"]}.html" data-status="{task["status"]}" '
        f'data-apps="{encoded_apps}" data-search="{html.escape(search_text)}">'
        f'<span class="status-dot {task["status"]}"></span><span class="task-main"><strong>{html.escape(task["name"])}</strong>'
        f'<small>{html.escape(task["goal"][:180])}</small>{apps_markup}</span><span class="badge {task["status"]}">{task["status"]}</span>'
        f'<span class="token-metrics"><b>{compact_number(usage["total"])}</b><small>tokens</small><span>I {compact_number(usage["input"])} · O {compact_number(usage["output"])} · C {cache_rate}</span></span>'
        f'<span class="metric">{task["steps"]}<small>steps</small></span><span class="row-arrow">{ICONS["next"]}</span></a>'
    )
  agent = metadata.get('agent')
  provider = metadata.get('provider')
  model_name = metadata.get('model')
  model = ' / '.join(
      value for value in (provider, model_name)
      if isinstance(value, str) and value
  )
  run_label = ' · '.join(
      value for value in (agent, model)
      if isinstance(value, str) and value
  ) or 'AndroidWorld run'
  app_options = ''.join(
      f'<option value="{html.escape(app_name, quote=True)}">'
      f'{html.escape(app_name)} ({count})</option>'
      for app_name, count in sorted(app_counts.items(), key=lambda item: item[0].lower())
  )
  app_filter = (
      '<label class="app-filter"><select id="appFilter" aria-label="Filter by app">'
      f'<option value="">All apps ({len(tasks)})</option>{app_options}</select></label>'
      if app_options else '')
  body = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(run_label)} · AndroidWorld trajectories</title><style>{CSS}</style></head><body><nav><span class="brand">{ICONS['android']}<span>AndroidWorld</span></span><span class="run-label">{html.escape(run_label)}</span></nav>
  <main class="index"><header class="index-header"><div><p class="eyebrow">Benchmark explorer</p><h1>Task trajectories</h1><p>Inspect every decision, action, and screen from this benchmark run.</p></div>
  <div class="stats"><div><b>{len(tasks)}</b><span>Tasks</span></div><div class="green"><b>{counts['success']}</b><span>Passed</span></div><div class="red"><b>{counts['failed'] + counts['partial'] + counts['exception']}</b><span>Issues</span></div></div></header>
  <section class="usage-strip benchmark-usage"><div><span>Total tokens</span><b>{int(aggregate['total']):,}</b><small>{int(aggregate['calls']):,} model calls</small></div><div><span>Input</span><b>{int(aggregate['input']):,}</b><small>uncached</small></div><div><span>Output</span><b>{int(aggregate['output']):,}</b><small>{int(aggregate['reasoning']):,} reasoning</small></div><div><span>Cache read</span><b>{int(aggregate['cacheRead']):,}</b><small>{int(aggregate['cacheWrite']):,} written</small></div><div><span>Cache rate</span><b>{aggregate_cache_rate:.1%}</b><small>cached / prompt</small></div></section>
  <section class="filters"><label class="search">{ICONS['search']}<input id="search" type="search" placeholder="Search tasks" autocomplete="off"></label>{app_filter}
  <div class="segments" id="filters"><button class="active" data-filter="all">All <span>{len(tasks)}</span></button><button data-filter="success">Passed <span>{counts['success']}</span></button><button data-filter="partial">Partial <span>{counts['partial']}</span></button><button data-filter="failed">Failed <span>{counts['failed']}</span></button><button data-filter="exception">Exception <span>{counts['exception']}</span></button><button data-filter="unknown">Unknown <span>{counts['unknown'] + counts['missing']}</span></button></div></section>
  <section class="task-list" id="tasks">{''.join(cards) or '<div class="empty">No tasks found in this result directory.</div>'}</section><p class="no-results" id="noResults">No matching tasks</p></main>
  <script>{INDEX_JS}</script></body></html>'''
  (output / 'index.html').write_text(body, encoding='utf8')


def remove_stale_task_pages(output: Path, live_slugs: set[str]) -> None:
  for page in output.glob('*.html'):
    if page.name == 'index.html' or page.stem in live_slugs:
      continue
    try:
      generated_by_report = 'Task trajectory' in page.read_text(encoding='utf8', errors='ignore')
    except OSError:
      continue
    if generated_by_report:
      page.unlink()


def generate_report(roots: list[Path], output: Path) -> int:
  output.mkdir(parents=True, exist_ok=True)
  app_names_by_task = load_registry_app_names([*roots, output])
  results: dict[str, dict[str, Any]] = {}
  by_name: dict[str, dict[str, Any]] = {}
  for root in roots:
    results.update(checkpoint_rows(root))
    goals_to_tasks = task_goal_map(root)
    sessions = load_sessions(root)
    if not sessions:
      sessions = load_worker_logs(root)
    run_entries: dict[str, dict[str, Any]] = {}
    for session in sessions:
      source = session['source']
      worker = next((part for part in source.parts if part.startswith('worker-')), '')
      name = goals_to_tasks.get(
          (worker, session['goal']), task_name_for(session['goal'], results))
      entry = run_entries.setdefault(
          name, {'goal': session['goal'], 'events': []})
      entry['events'].extend(session['events'])
    by_name.update(run_entries)
  for name in results:
    by_name.setdefault(name, {'goal': name, 'events': []})
  tasks = []
  for name, entry in sorted(by_name.items()):
    result = results.get(name)
    app_names = app_names_by_task.get(name, [])
    slug, steps = render_task(
        name, entry['goal'], entry['events'], result, roots[-1], output,
        app_names=app_names)
    tasks.append({'name': name, 'goal': entry['goal'], 'slug': slug, 'steps': steps,
                  'status': status_for(result, bool(entry['events'])),
                  'usage': token_usage(entry['events']),
                  'app_names': app_names})
  remove_stale_task_pages(output, {task['slug'] for task in tasks})
  try:
    metadata = json.loads((roots[-1] / 'manifest.json').read_text(encoding='utf8'))
  except (FileNotFoundError, json.JSONDecodeError):
    metadata = {}
  render_index(tasks, output, metadata)
  print(f'Generated {len(tasks)} task pages in {output}')
  return len(tasks)


def main() -> None:
  args = parse_args()
  roots = [Path(value).resolve() for value in args.result_dir]
  output = Path(args.output_dir).resolve()
  missing = [root for root in roots if not root.is_dir()]
  if missing:
    raise SystemExit(f'Result directory does not exist: {missing[0]}')
  generate_report(roots, output)


ICONS = {
    'android': '<svg viewBox="0 0 24 24"><path d="M8 6 6.7 3.8M16 6l1.3-2.2M5 9h14v9a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V9Zm3 4h.01M16 13h.01"/></svg>',
    'back': '<svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>',
    'prev': '<svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>',
    'next': '<svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>',
    'close': '<svg viewBox="0 0 24 24"><path d="m18 6-12 12M6 6l12 12"/></svg>',
    'expand': '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    'search': '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>',
    'thinking': '<svg viewBox="0 0 24 24"><path d="M9.5 4a3 3 0 0 0-2.9 3.8A3.5 3.5 0 0 0 7 14.5V17a3 3 0 0 0 5 2.2V6.5A2.5 2.5 0 0 0 9.5 4ZM14.5 4a3 3 0 0 1 2.9 3.8 3.5 3.5 0 0 1-.4 6.7V17a3 3 0 0 1-5 2.2"/></svg>',
    'action': '<svg viewBox="0 0 24 24"><path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"/></svg>',
    'observation': '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg>',
    'log': '<svg viewBox="0 0 24 24"><path d="M4 5h16v14H4zM8 9l2 2-2 2M12 13h4"/></svg>',
    'meta': '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
}


INDEX_JS = r'''
const search = document.querySelector('#search');
const appFilter = document.querySelector('#appFilter');
const rows = [...document.querySelectorAll('.task-row')];
const filterButtons = [...document.querySelectorAll('#filters button')];
const stateKey = `androidworld-report:${location.pathname}`;
let savedQuery = '';
try { savedQuery = sessionStorage.getItem(stateKey) || ''; } catch {}
const initialParams = new URLSearchParams(location.search || savedQuery);
search.value = initialParams.get('q') || '';
const requestedApp = initialParams.get('app') || '';
if (appFilter && [...appFilter.options].some(option => option.value === requestedApp)) {
  appFilter.value = requestedApp;
}
const requestedStatus = initialParams.get('status') || 'all';
const initialStatusButton = filterButtons.find(button => button.dataset.filter === requestedStatus)
  || filterButtons.find(button => button.dataset.filter === 'all');
let filter = initialStatusButton?.dataset.filter || 'all';
filterButtons.forEach(button => button.classList.toggle('active', button === initialStatusButton));
function persistFilters() {
  const params = new URLSearchParams();
  const query = search.value.trim();
  const selectedApp = appFilter?.value || '';
  if (query) params.set('q', query);
  if (selectedApp) params.set('app', selectedApp);
  if (filter !== 'all') params.set('status', filter);
  const serialized = params.toString();
  try {
    const url = new URL(location.href);
    url.search = serialized;
    history.replaceState(null, '', url);
  } catch {}
  try { sessionStorage.setItem(stateKey, serialized ? `?${serialized}` : ''); } catch {}
}
function applyFilters() {
  const query = search.value.trim().toLowerCase(); let visible = 0;
  const selectedApp = appFilter?.value || '';
  rows.forEach(row => {
    const status = row.dataset.status;
    const statusMatch = filter === 'all' || status === filter || (filter === 'unknown' && status === 'missing');
    const rowApps = JSON.parse(row.dataset.apps || '[]');
    const appMatch = !selectedApp || rowApps.includes(selectedApp);
    const show = statusMatch && appMatch && row.dataset.search.includes(query);
    row.hidden = !show; if (show) visible++;
  });
  document.querySelector('#noResults').hidden = visible !== 0;
  persistFilters();
}
search.addEventListener('input', applyFilters);
appFilter?.addEventListener('change', applyFilters);
document.querySelector('#filters').addEventListener('click', event => {
  const button = event.target.closest('button'); if (!button) return;
  filterButtons.forEach(item => item.classList.toggle('active', item === button));
  filter = button.dataset.filter; applyFilters();
});
applyFilters();
'''


DETAIL_JS = r'''
const indexUrl = new URL('index.html', location.href);
const indexStateKey = `androidworld-report:${indexUrl.pathname}`;
let indexQuery = '';
try { indexQuery = sessionStorage.getItem(indexStateKey) || ''; } catch {}
if (indexQuery) indexUrl.search = indexQuery;
document.querySelectorAll('a[href="index.html"]').forEach(link => { link.href = indexUrl.href; });
const cards = [...document.querySelectorAll('.event')];
let current = 0;
function select(index) {
  if (!cards.length) return;
  current = Math.max(0, Math.min(index, cards.length - 1));
  cards.forEach(card => card.classList.remove('selected'));
  const card = cards[current]; card.classList.add('selected');
  card.scrollIntoView({behavior: 'smooth', block: 'center'});
  const image = card.dataset.image; const screen = document.querySelector('#screen');
  screen.src = image || ''; screen.hidden = !image; document.querySelector('#noScreen').hidden = !!image;
}
cards.forEach(card => card.addEventListener('click', () => select(cards.indexOf(card))));
document.querySelectorAll('.event-toggle').forEach(button => button.addEventListener('click', event => {
  event.stopPropagation();
  const card = button.closest('.event');
  const expanded = card.classList.toggle('open');
  button.setAttribute('aria-expanded', String(expanded));
  button.title = expanded ? 'Collapse event' : 'Expand event';
}));
const dialog = document.querySelector('#lightbox');
document.querySelector('#imageButton').onclick = () => { const src = document.querySelector('#screen').src; if (!src) return; document.querySelector('#fullScreen').src = src; dialog.showModal(); };
document.querySelector('#close').onclick = () => dialog.close();
dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
document.addEventListener('keydown', event => {
  if (event.key === 'ArrowDown' || event.key === 'ArrowRight') select(current + 1);
  if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') select(current - 1);
  if (event.key === 'Escape' && dialog.open) dialog.close();
});
select(0);
'''


CSS = r'''
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:#172026;background:#f5f7f8;line-height:1.5;font-size:15px}*{box-sizing:border-box}body{margin:0}svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}nav{height:58px;background:#fff;border-bottom:1px solid #dfe5e8;display:flex;align-items:center;padding:0 max(24px,calc((100vw - 1240px)/2));gap:24px;position:sticky;top:0;z-index:20}.brand{display:flex;align-items:center;gap:9px;color:#172026;text-decoration:none;font-weight:750}.brand svg{color:#247b56;width:23px;height:23px}.back{margin-left:auto;display:flex;align-items:center;color:#53616a;text-decoration:none}.run-label{margin-left:auto;color:#69777f;font-size:13px}main{max-width:1240px;margin:auto;padding:38px 24px 70px}.eyebrow{text-transform:uppercase;color:#247b56;font-size:11px;font-weight:800;letter-spacing:.13em;margin:0 0 7px}h1{font-size:36px;line-height:1.15;letter-spacing:0;margin:0 0 12px}.index-header{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:30px}.index-header p:last-child{margin:0;color:#69777f}.stats{display:flex;background:#fff;border:1px solid #dfe5e8}.stats div{min-width:105px;padding:14px 20px;border-left:1px solid #dfe5e8;display:flex;flex-direction:column}.stats div:first-child{border:0}.stats b{font-size:22px}.stats span{color:#7b888f;font-size:12px}.stats .green b{color:#247b56}.stats .red b{color:#b94a45}.filters{display:flex;align-items:center;gap:14px;padding:15px 0;border-top:1px solid #dfe5e8;border-bottom:1px solid #dfe5e8;margin-bottom:12px}.search{height:38px;width:270px;background:#fff;border:1px solid #cfd7db;display:flex;align-items:center;padding:0 11px;gap:8px}.search svg{color:#7b888f;width:17px}.search input{border:0;outline:0;width:100%;font:inherit}.app-filter select{height:38px;min-width:190px;border:1px solid #cfd7db;background:#fff;color:#35434b;padding:0 32px 0 10px;font:inherit}.segments{display:flex;gap:5px;overflow:auto;margin-left:auto}.segments button,.toolbar button{border:0;background:transparent;font:inherit;color:#69777f;cursor:pointer}.segments button{height:34px;padding:0 10px;white-space:nowrap}.segments button.active{background:#e6f1eb;color:#176843;font-weight:700}.segments span{margin-left:4px;opacity:.7}.task-list{background:#fff;border:1px solid #dfe5e8}.task-row{display:grid;grid-template-columns:16px minmax(0,1fr) 90px 70px 24px;align-items:center;gap:15px;padding:15px 18px;border-bottom:1px solid #e5eaec;color:#172026;text-decoration:none;transition:background .15s}.task-row:last-child{border:0}.task-row:hover{background:#f7faf8}.task-row[hidden]{display:none}.status-dot{width:8px;height:8px;border-radius:50%;background:#9aa5aa}.status-dot.success{background:#27835d}.status-dot.partial{background:#387fa3}.status-dot.failed{background:#d29332}.status-dot.exception{background:#c7524d}.task-main{min-width:0;display:flex;flex-direction:column}.task-main strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.task-main small{color:#7b888f;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.task-apps{color:#52646d;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.task-apps b{color:#247b56;font-size:9px;text-transform:uppercase;margin-right:6px}.task-apps-detail{margin:9px 0 0;color:#52646d;font-size:12px}.task-apps-detail span{color:#247b56;font-size:10px;font-weight:800;text-transform:uppercase;margin-right:8px}.badge{justify-self:start;text-transform:capitalize;font-size:11px;font-weight:800;padding:3px 7px;background:#edf0f1;color:#69777f}.badge.success{background:#dff1e7;color:#176843}.badge.partial{background:#dfeef5;color:#276b8c}.badge.failed{background:#f7ead5;color:#8a5b14}.badge.exception{background:#f7dfdd;color:#9e332e}.metric{text-align:right;font-weight:700}.metric small{display:block;color:#89949a;font-weight:400;font-size:10px}.row-arrow{color:#89949a}.no-results,.no-results[hidden]{display:none}.no-results:not([hidden]){display:block;text-align:center;padding:50px;color:#7b888f}.task-header{display:flex;gap:30px;justify-content:space-between;padding-bottom:24px;border-bottom:1px solid #dfe5e8}.task-header>div:first-child{max-width:760px}.goal{color:#5f6c73;margin:0;white-space:pre-line}.summary{display:flex;align-items:flex-start;gap:14px;color:#69777f;font-size:12px;white-space:nowrap}.summary>span:not(.badge){display:flex;flex-direction:column}.summary b{color:#172026;font-size:15px}.toolbar{height:56px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #dfe5e8;position:sticky;top:58px;background:#f5f7f8;z-index:10}.toolbar button{width:34px;height:34px;display:grid;place-items:center}.toolbar button:hover{background:#e8edef}.toolbar button:disabled{opacity:.25}.toolbar #position{font-size:12px;color:#69777f;min-width:88px;text-align:center}.toolbar .text-button{width:auto;margin-left:auto;padding:0 10px}.trajectory{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:40px;padding-top:22px}.events{position:relative}.events:before{content:"";position:absolute;top:16px;bottom:16px;left:20px;width:1px;background:#d6dde0}.event{position:relative;background:#fff;border:1px solid #dfe5e8;margin-bottom:12px;padding:15px 16px 15px 54px;cursor:pointer;transition:border-color .15s,box-shadow .15s}.event.selected{border-color:#5a947a;box-shadow:0 0 0 2px #d9ebe2}.event-head{display:flex;align-items:center;gap:8px}.event-icon{position:absolute;left:8px;width:25px;height:25px;border-radius:50%;background:#eef2f3;display:grid;place-items:center;z-index:2}.event-icon svg{width:14px}.thinking .event-icon{color:#276a9a;background:#e2eff7}.action .event-icon{color:#9a681e;background:#f7ecd9}.observation .event-icon{color:#247b56;background:#e0f0e7}.event time{margin-left:auto;color:#8a969c;font-size:11px}.event pre{margin:11px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;color:#506068;font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;max-height:118px;overflow:hidden}.events.expanded .event pre{max-height:none;overflow:auto}.phone-panel{position:sticky;top:132px;align-self:start;text-align:center}.phone{width:290px;height:586px;margin:auto;background:#171b1d;border:7px solid #171b1d;border-radius:32px;box-shadow:0 16px 40px #24332b24;overflow:hidden;position:relative}.phone-top{position:absolute;z-index:2;top:7px;left:50%;transform:translateX(-50%);width:56px;height:5px;background:#303638;border-radius:5px}.phone button{border:0;padding:0;background:#0d1011;width:100%;height:100%;cursor:zoom-in;display:grid;place-items:center}.phone img{width:100%;height:100%;object-fit:contain}.phone img[hidden]{display:none}.phone #noScreen{color:#879197;font-size:12px;padding:20px}.phone #noScreen[hidden]{display:none}.phone-panel p{font-size:11px;color:#89949a}.empty{text-align:center;color:#7b888f;padding:50px;background:#fff;border:1px solid #dfe5e8}dialog{border:0;padding:0;background:transparent;max-width:95vw;max-height:95vh}dialog::backdrop{background:#0b0f10e6}dialog img{display:block;max-width:88vw;max-height:92vh}dialog button{position:fixed;right:22px;top:22px;width:40px;height:40px;border:0;background:#fff;color:#172026;display:grid;place-items:center;cursor:pointer}
.usage-strip{display:grid;grid-template-columns:repeat(5,1fr);background:#fff;border:1px solid #dfe5e8;margin:18px 0 0}.usage-strip>div{padding:13px 16px;border-left:1px solid #e2e7e9;display:flex;flex-direction:column;min-width:0}.usage-strip>div:first-child{border-left:0}.usage-strip span,.usage-strip small{font-size:10px;color:#7b888f;text-transform:uppercase}.usage-strip b{font-size:17px;overflow:hidden;text-overflow:ellipsis}.usage-strip small{text-transform:none}.benchmark-usage{margin:0 0 20px}.task-row{grid-template-columns:16px minmax(0,1fr) 90px 145px 58px 24px}.token-metrics{display:grid;grid-template-columns:auto 1fr;align-items:baseline;column-gap:5px;white-space:nowrap}.token-metrics>b{font-size:14px}.token-metrics>small{font-size:10px;color:#89949a}.token-metrics>span{grid-column:1/-1;color:#7b888f;font-size:10px}.event-usage{display:flex;flex-wrap:wrap;gap:5px 14px;margin-top:11px;padding-top:9px;border-top:1px solid #edf0f1;color:#7b888f;font-size:10px;text-transform:uppercase}.event-usage b{color:#506068}.step-number{font-size:10px;font-weight:800;color:#247b56;text-transform:uppercase;white-space:nowrap}.event-toggle{width:28px;height:28px;margin-left:2px;border:0;background:transparent;color:#7b888f;display:grid;place-items:center;cursor:pointer}.event-toggle:hover{background:#edf1f2}.event-toggle svg{width:16px;transition:transform .15s}.event.open .event-toggle svg{transform:rotate(180deg)}.event.open pre{max-height:none;overflow:auto}.phone-panel{top:78px}
@media(max-width:850px){nav{padding:0 16px}main{padding:25px 16px 50px}.index-header,.task-header{display:block}.stats{margin-top:24px;width:max-content}.filters{align-items:stretch;flex-direction:column}.search,.app-filter select{width:100%}.segments{margin-left:0}.task-row{grid-template-columns:14px minmax(0,1fr) 70px 20px}.task-row .badge,.task-row .token-metrics{display:none}.usage-strip{grid-template-columns:repeat(2,1fr)}.usage-strip>div{border-bottom:1px solid #e2e7e9}.trajectory{grid-template-columns:1fr;gap:20px}.phone-panel{position:relative;top:auto;grid-row:1}.phone{width:220px;height:445px}.summary{margin-top:18px;flex-wrap:wrap}h1{font-size:28px}.run-label{max-width:50%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
'''


if __name__ == '__main__':
  main()
