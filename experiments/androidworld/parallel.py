"""Run a sharded AndroidWorld experiment from one TOML configuration."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, TextIO

from dotenv import dotenv_values

from .config import AndroidWorldConfig, load_config


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('config', help='experiment TOML file')
  parser.add_argument(
      '--dry-run', action='store_true',
      help='validate, resolve tasks, and print commands without starting workers',
  )
  return parser.parse_args()


def main() -> None:
  args = parse_args()
  config = load_config(args.config)
  project_dir = Path(__file__).resolve().parents[2]
  print(f'Loading task registry from {config.image} ...', flush=True)
  task_registry = _load_task_registry(config.image, config.suite.family)
  if config.suite.tasks:
    missing = sorted(set(config.suite.tasks) - task_registry.keys())
    if missing:
      raise ValueError(f'Tasks missing from container registry: {missing}')
    task_registry = {
        name: task_registry[name] for name in config.suite.tasks
    }
  if len(task_registry) < config.experiment.workers:
    raise ValueError(
        f'Cannot assign {len(task_registry)} tasks to '
        f'{config.experiment.workers} workers without empty shards'
    )
  shards = _balanced_shards(task_registry, config.experiment.workers)
  timestamp = dt.datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')
  output_dir = (
      project_dir / config.experiment.output_root /
      f'{config.experiment.name}-{timestamp}'
  ).resolve()

  if args.dry_run:
    preview = _prepare_workers(config, project_dir, output_dir, shards, write=False)
    print(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
    return

  output_dir.mkdir(parents=True)
  print(
      f'Starting {config.experiment.name}: {len(shards)} workers, '
      f'{len(task_registry)} task templates',
      flush=True,
  )
  print(f'Results: {output_dir}', flush=True)
  workers = _start_workers(config, project_dir, output_dir, shards)
  manifest = _manifest(config, task_registry, workers)
  _write_json(output_dir / 'manifest.json', manifest)

  stopping = False

  def stop_workers(_signum=None, _frame=None) -> None:
    nonlocal stopping
    if stopping:
      for worker in workers:
        subprocess.run(
            ['docker', 'rm', '-f', worker['container_name']], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
      return
    stopping = True
    for worker in workers:
      process = worker['process']
      if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)

  signal.signal(signal.SIGINT, stop_workers)
  signal.signal(signal.SIGTERM, stop_workers)
  try:
    while any(worker['process'].poll() is None for worker in workers):
      _write_status(output_dir, workers, manifest['status'])
      running = sum(worker['process'].poll() is None for worker in workers)
      checkpoints = sum(
          len(list((worker['worker_dir'] / 'checkpoints').glob('*.pkl.gz')))
          for worker in workers
      )
      print(
          f'[progress] {running}/{len(workers)} workers running; '
          f'{checkpoints}/{manifest["expected_episodes"]} episodes recorded',
          flush=True,
      )
      time.sleep(15)
  finally:
    for worker in workers:
      worker['process'].wait()
      worker['output_thread'].join()
      worker['log_handle'].close()

  summary = _summarize(output_dir, config.image, workers, manifest['expected_episodes'])
  manifest['status'] = 'completed' if summary['worker_failures'] == 0 else 'failed'
  manifest['finished_at'] = dt.datetime.now().astimezone().isoformat()
  manifest['summary'] = summary
  _write_json(output_dir / 'manifest.json', manifest)
  _write_status(output_dir, workers, manifest['status'])
  _write_results(output_dir / 'results.md', manifest)
  if config.container.keep_containers:
    names = ', '.join(worker['container_name'] for worker in workers)
    print(f'Retained stopped containers: {names}', flush=True)
  print(json.dumps(summary, ensure_ascii=False, indent=2))


def _prepare_workers(
    config: AndroidWorldConfig, project_dir: Path, output_dir: Path,
    shards: list[list[str]], *, write: bool,
) -> list[dict[str, Any]]:
  prepared = []
  for index, shard in enumerate(shards, start=1):
    worker_dir = output_dir / f'worker-{index}'
    config_path = worker_dir / 'config.json'
    if write:
      worker_dir.mkdir(parents=True)
      # These paths are written by coding-agent processes after they drop root.
      # Create them on the host so container root cannot claim their ownership.
      for directory in ('checkpoints', 'ledgers', 'runs', 'learning'):
        (worker_dir / directory).mkdir()
      _write_json(config_path, config.worker_payload(shard))
    agent_config_dir = _prepare_agent_config(config, worker_dir, write=write)
    if config.container.keep_containers:
      run_id = ''.join(
          character if character.isalnum() or character in '_.-' else '-'
          for character in output_dir.name
      )
      container_name = f'{config.container.name_prefix}-{run_id}-{index}'
    else:
      container_name = f'{config.container.name_prefix}-{index}'
    command = _docker_command(
        config, project_dir, worker_dir, config_path, container_name,
        create_cache=write, agent_config_dir=agent_config_dir,
    )
    prepared.append({
        'worker_id': index,
        'container_name': container_name,
        'tasks': shard,
        'worker_dir': worker_dir,
        'config_path': config_path,
        'command': command,
    })
  return prepared


def _start_workers(
    config: AndroidWorldConfig, project_dir: Path, output_dir: Path,
    shards: list[list[str]],
) -> list[dict[str, Any]]:
  workers = _prepare_workers(config, project_dir, output_dir, shards, write=True)
  started = []
  try:
    for worker in workers:
      log_path = worker['worker_dir'] / 'worker.log'
      log_handle = log_path.open('w', encoding='utf8')
      try:
        process = subprocess.Popen(
            worker['command'], cwd=project_dir,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, text=True, encoding='utf8', errors='replace',
            bufsize=1,
        )
      except Exception:
        log_handle.close()
        raise
      assert process.stdout is not None
      output_thread = threading.Thread(
          target=_relay_worker_output,
          args=(worker['worker_id'], process.stdout, log_handle),
          name=f"androidworld-worker-{worker['worker_id']}-output",
          daemon=True,
      )
      worker.update({
          'process': process,
          'pid': process.pid,
          'log_path': log_path,
          'log_handle': log_handle,
          'output_thread': output_thread,
      })
      print(
          f"[worker-{worker['worker_id']}] started container "
          f"{worker['container_name']} with {len(worker['tasks'])} tasks; "
          f'log: {log_path}',
          flush=True,
      )
      output_thread.start()
      started.append(worker)
  except Exception:
    for worker in started:
      worker['process'].terminate()
      subprocess.run(
          ['docker', 'rm', '-f', worker['container_name']], check=False,
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
      )
      worker['process'].wait()
      worker['output_thread'].join()
      worker['log_handle'].close()
    raise
  return workers


def _relay_worker_output(worker_id: int, stream: TextIO, log: TextIO) -> None:
  """Tee one worker's output to its log and the shared terminal."""
  try:
    for line in stream:
      log.write(line)
      log.flush()
      print(f'[worker-{worker_id}] {line}', end='', flush=True)
  finally:
    stream.close()


def _docker_command(
    config: AndroidWorldConfig, project_dir: Path, worker_dir: Path,
    config_path: Path, container_name: str, *, create_cache: bool = True,
    agent_config_dir: Path | None = None,
) -> list[str]:
  command = [
      'docker', 'run',
  ]
  if not config.container.keep_containers:
    command.append('--rm')
  command.extend([
      '--privileged', '--device', '/dev/kvm',
      '--name', container_name,
      '-e', 'PYTHONPATH=/workspace/pi-gui-agent:/',
      '-e', 'PYTHONUNBUFFERED=1',
      '-e', f'ANDROIDWORLD_HOST_UID={os.getuid()}',
      '-e', f'ANDROIDWORLD_HOST_GID={os.getgid()}',
      '-v', f'{project_dir}:/workspace/pi-gui-agent:ro',
      '-v', f'{worker_dir}:/output',
      '-w', '/workspace/pi-gui-agent',
  ])
  if config.container.proxy_url:
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
      command.extend(['-e', f'{name}={config.container.proxy_url}'])
    command.extend(['-e', 'NO_PROXY=127.0.0.1,localhost,::1'])
  if config.container.env_file:
    env_file = _project_path(project_dir, config.container.env_file)
    if not env_file.is_file():
      raise FileNotFoundError(f'Container env file does not exist: {env_file}')
    command.extend(['--env-file', str(env_file)])
    env_file_names = set(dotenv_values(env_file))
  else:
    env_file_names = set()
  for name in config.container.forward_env:
    if name in os.environ:
      command.extend(['-e', name])
    elif name not in env_file_names:
      raise ValueError(
          f'Required forwarded environment variable is missing: {name}'
      )
  config_dir = agent_config_dir if agent_config_dir is not None else _agent_config_dir(config)
  if config_dir:
    home_name = {
        'claude-code': '.claude', 'codex': '.codex', 'openclaw': '.openclaw',
    }[config.agent.name]
    mode = 'rw' if config.agent.name == 'openclaw' else 'ro'
    command.extend(['-v', f'{config_dir}:/home/agent/{home_name}:{mode}'])
  if config.container.download_cache_dir:
    cache = Path(config.container.download_cache_dir).expanduser().resolve()
    if create_cache:
      cache.mkdir(parents=True, exist_ok=True)
    command.extend([
        '-e', 'ANDROID_WORLD_DOWNLOAD_CACHE_DIR=/download-cache',
        '-v', f'{cache}:/download-cache:ro',
    ])
  command.extend([
      config.image,
      'python3', '-m', 'experiments.androidworld.run',
      f'/output/{config_path.name}',
  ])
  return command


def _prepare_agent_config(
    config: AndroidWorldConfig, worker_dir: Path, *, write: bool,
) -> Path | None:
  """Give stateful baseline CLIs an isolated writable config directory."""
  source = _agent_config_dir(config)
  if source is None or config.agent.name != 'openclaw':
    return source
  target = worker_dir / '.openclaw'
  if not write:
    return source
  shutil.copytree(source, target, dirs_exist_ok=True)
  return target


def _project_path(project_dir: Path, value: str) -> Path:
  path = Path(value).expanduser()
  return path.resolve() if path.is_absolute() else (project_dir / path).resolve()


def _agent_config_dir(config: AndroidWorldConfig) -> Path | None:
  if not config.container.agent_config_dir or config.agent.name == 'pi-gui':
    return None
  path = Path(config.container.agent_config_dir).expanduser().resolve()
  if not path.is_dir():
    raise FileNotFoundError(f'Agent config directory does not exist: {path}')
  return path


def _load_task_registry(image: str, family: str) -> dict[str, float]:
  script = (
      'import json; from android_world import registry; '
      f'r=registry.TaskRegistry().get_registry({family!r}); '
      'print(json.dumps({n:float(getattr(c,"complexity",1)) for n,c in r.items()}))'
  )
  result = subprocess.run(
      ['docker', 'run', '--rm', '--entrypoint', 'python3', image, '-c', script],
      check=True, capture_output=True, text=True,
  )
  return json.loads(result.stdout)


def _balanced_shards(registry: dict[str, float], count: int) -> list[list[str]]:
  shards: list[list[str]] = [[] for _ in range(count)]
  totals = [0.0] * count
  for name in sorted(registry, key=lambda item: (-registry[item], item)):
    index = min(range(count), key=lambda item: (totals[item], len(shards[item]), item))
    shards[index].append(name)
    totals[index] += registry[name]
  for shard in shards:
    shard.sort()
  return shards


def _manifest(
    config: AndroidWorldConfig, registry: dict[str, float],
    workers: list[dict[str, Any]],
) -> dict[str, Any]:
  return {
      'started_at': dt.datetime.now().astimezone().isoformat(),
      'status': 'running',
      'config': str(config.source),
      'agent': config.agent.name,
      'provider': config.agent.provider,
      'model': config.agent.model,
      'thinking': config.agent.thinking,
      'learning': config.agent.learning,
      'setup_mode': config.suite.setup_mode,
      'container_image': config.image,
      'task_template_count': len(registry),
      'expected_episodes': len(registry) * config.suite.combinations,
      'workers': [{
          'worker_id': worker['worker_id'],
          'pid': worker['pid'],
          'container_name': worker['container_name'],
          'tasks': worker['tasks'],
          'command': worker['command'],
      } for worker in workers],
  }


def _write_status(output_dir: Path, workers: list[dict[str, Any]], status: str) -> None:
  _write_json(output_dir / 'status.json', {
      'updated_at': dt.datetime.now().astimezone().isoformat(),
      'experiment_status': status,
      'workers': [{
          'worker_id': worker['worker_id'],
          'running': worker['process'].poll() is None,
          'returncode': worker['process'].poll(),
          'checkpoints': len(list((worker['worker_dir'] / 'checkpoints').glob('*.pkl.gz'))),
      } for worker in workers],
  })


def _summarize(
    output_dir: Path, image: str, workers: list[dict[str, Any]], expected: int,
) -> dict[str, Any]:
  summary_path = output_dir / '.summary.json'
  script = '''
import glob, json
from android_world import checkpointer, constants
episodes = []
for path in glob.glob('/output/worker-*/checkpoints'):
  episodes.extend(checkpointer.IncrementalCheckpointer(path).load())
completed = [e for e in episodes if e.get(constants.EpisodeConstants.EXCEPTION_INFO) is None]
successes = sum(float(e.get(constants.EpisodeConstants.IS_SUCCESSFUL, 0) or 0) for e in completed)
json.dump({
  'recorded_episodes': len(episodes),
  'completed_episodes': len(completed),
  'exception_episodes': len(episodes) - len(completed),
  'successful_episodes': successes,
  'success_rate': successes / len(completed) if completed else None,
  'total_runtime_seconds': sum(float(e.get(constants.EpisodeConstants.RUN_TIME, 0) or 0) for e in episodes),
}, open('/output/.summary.json', 'w'))
'''
  subprocess.run(
      ['docker', 'run', '--rm', '--entrypoint', 'python3',
       '-v', f'{output_dir}:/output', image, '-c', script],
      check=True,
  )
  summary = json.loads(summary_path.read_text(encoding='utf8'))
  summary_path.unlink()
  summary['expected_episodes'] = expected
  summary['worker_failures'] = sum(worker['process'].returncode != 0 for worker in workers)
  return summary


def _write_results(path: Path, manifest: dict[str, Any]) -> None:
  summary = manifest['summary']
  rate = summary['success_rate']
  lines = [
      '# AndroidWorld Results', '',
      f"- Status: {manifest['status']}",
      f"- Agent: `{manifest['agent']}`",
      f"- Model: `{manifest['provider'] or 'agent default'}/{manifest['model'] or 'agent default'}`",
      f"- Expected episodes: {summary['expected_episodes']}",
      f"- Recorded episodes: {summary['recorded_episodes']}",
      f"- Completed episodes: {summary['completed_episodes']}",
      f"- Exception episodes: {summary['exception_episodes']}",
      f"- Successful episodes: {summary['successful_episodes']}",
      f"- Success rate: {rate:.2%}" if rate is not None else '- Success rate: n/a',
      f"- Total episode runtime (s): {summary['total_runtime_seconds']:.1f}", '',
  ]
  path.write_text('\n'.join(lines), encoding='utf8')


def _write_json(path: Path, value: Any) -> None:
  temporary = path.with_suffix(path.suffix + '.tmp')
  temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
  temporary.replace(path)


if __name__ == '__main__':
  main()
