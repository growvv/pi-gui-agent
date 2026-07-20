"""Run a concurrent MobileWorld experiment from one TOML configuration."""

from __future__ import annotations

import argparse
from dataclasses import replace
import datetime as dt
import json
import os
from pathlib import Path
import signal
import select
import socket
import socketserver
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values

from .config import MobileWorldConfig, load_config


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('config', help='experiment TOML file')
  parser.add_argument('--dry-run', action='store_true', help='print commands only')
  return parser.parse_args()


def main() -> None:
  args = parse_args()
  config = load_config(args.config)
  image_override = os.environ.get('MOBILEWORLD_AGENT_IMAGE')
  if image_override:
    config = replace(
        config, mobileworld=replace(config.mobileworld, image=image_override),
    )
  project_dir = Path(__file__).resolve().parents[2]
  source_dir_value = os.environ.get('MOBILEWORLD_ROOT')
  if not source_dir_value:
    raise RuntimeError(
        'MOBILEWORLD_ROOT must point to an official MobileWorld checkout '
        'when running the host MobileWorld controller'
    )
  source_dir = Path(source_dir_value).resolve()
  output_dir = _output_dir(config)
  credential_file = _credential_file(project_dir)
  prefix = (
      config.mobileworld.name_prefix if config.mobileworld.reuse_containers
      else f'{config.mobileworld.name_prefix}_{os.getpid()}'
  )
  env_command = _environment_command(config, source_dir, prefix, credential_file)
  eval_command = _evaluation_command(config, source_dir, output_dir, prefix, project_dir)

  if args.dry_run:
    print(json.dumps({
        'environment': env_command, 'evaluation': eval_command,
        'output_dir': str(output_dir),
    }, ensure_ascii=False, indent=2))
    return

  _validate_runtime(source_dir, config.mobileworld.image)
  output_dir.mkdir(parents=True)
  manifest: dict[str, Any] = {
      'started_at': dt.datetime.now().astimezone().isoformat(),
      'status': 'starting', 'config': config.public_config(),
      'mobileworld_image_id': _image_id(config.mobileworld.image),
      'container_prefix': prefix, 'environment_command': env_command,
      'evaluation_command': eval_command,
  }
  _write_json(output_dir / 'manifest.json', manifest)

  stopping = False
  proxy_relay = _start_proxy_relay(
      config.mobileworld.proxy_url, config.mobileworld.proxy_relay_port,
  )

  def cleanup(_signum=None, _frame=None) -> None:
    nonlocal stopping
    if stopping:
      return
    stopping = True
    if not config.mobileworld.keep_containers:
      _remove_containers(prefix, config.experiment.workers)

  def stop(signum, frame) -> None:
    cleanup(signum, frame)
    raise SystemExit(128 + signum)

  signal.signal(signal.SIGINT, stop)
  signal.signal(signal.SIGTERM, stop)
  try:
    containers = _ready_container_names(
        prefix, config.mobileworld.image, config.experiment.workers,
    )
    reused = config.mobileworld.reuse_containers and len(containers) == config.experiment.workers
    if not reused:
      if config.mobileworld.reuse_containers:
        _remove_containers(prefix, config.experiment.workers)
      try:
        subprocess.run(
            env_command, cwd=source_dir, check=True,
            timeout=config.mobileworld.startup_timeout_seconds,
        )
      except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        _print_container_diagnostics(prefix, config.experiment.workers)
        _remove_containers(prefix, config.experiment.workers)
        raise
      containers = _wait_for_ready_containers(
          prefix, config.mobileworld.image, config.experiment.workers,
      )
    if len(containers) != config.experiment.workers:
      raise RuntimeError(
          f'Expected {config.experiment.workers} containers, found {containers}'
      )
    manifest['containers'] = containers
    manifest['reused_containers'] = reused
    manifest['status'] = 'running'
    _write_json(output_dir / 'manifest.json', manifest)
    result = subprocess.run(
        eval_command, cwd=source_dir,
        env=_agent_environment(
            config, project_dir, output_dir, credential_file, prefix,
        ),
        check=False,
    )
    summary = _summarize(output_dir, len(config.suite.tasks) or None)
    summary['evaluator_returncode'] = result.returncode
    complete = result.returncode == 0 and summary['missing_tasks'] == 0
    manifest['status'] = 'completed' if complete else 'failed'
    manifest['finished_at'] = dt.datetime.now().astimezone().isoformat()
    manifest['summary'] = summary
    _write_json(output_dir / 'manifest.json', manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not complete:
      raise SystemExit(result.returncode or 1)
  finally:
    cleanup()
    if proxy_relay:
      proxy_relay.shutdown()
      proxy_relay.server_close()


def _environment_command(
    config: MobileWorldConfig, source_dir: Path, prefix: str,
    credential_file: Path | None = None,
) -> list[str]:
  settings = config.mobileworld
  command = [
      'uv', 'run', '--no-sync', '--project', str(source_dir), 'mw', 'env', 'run',
      '--count', str(config.experiment.workers),
      '--image', settings.image, '--name-prefix', prefix,
      '--launch-interval', str(settings.launch_interval_seconds),
      '--backend-start-port', str(settings.backend_start_port),
      '--viewer-start-port', str(settings.viewer_start_port),
      '--vnc-start-port', str(settings.vnc_start_port),
  ]
  if credential_file and credential_file.is_file():
    command.extend(['--env-file', str(credential_file)])
  return command


def _evaluation_command(
    config: MobileWorldConfig, source_dir: Path, output_dir: Path,
    prefix: str, project_dir: Path,
) -> list[str]:
  tasks = ','.join(config.suite.tasks) if config.suite.tasks else 'ALL'
  command = [
      'uv', 'run', '--no-sync', '--project', str(source_dir), 'mw', 'eval',
      '--agent-type', str(project_dir / 'experiments' / 'mobileworld' / 'agent.py'),
      '--task', tasks, '--max-round', '1',
      '--aw-host', ','.join(
          f'http://localhost:{config.mobileworld.backend_start_port + index}'
          for index in range(config.experiment.workers)
      ),
      '--max-concurrency', str(config.experiment.workers),
      '--max-retries', str(config.suite.max_retries),
      '--step-wait-time', str(config.suite.step_wait_seconds),
      '--env-name-prefix', prefix, '--env-image', config.mobileworld.image,
      '--log-file-root', str(output_dir / 'trajectories'),
  ]
  if config.agent.model:
    command.extend(['--model-name', config.agent.model])
  return command


def _agent_environment(
    config: MobileWorldConfig, project_dir: Path, output_dir: Path,
    credential_file: Path | None = None, prefix: str | None = None,
) -> dict[str, str]:
  values = {
      'PI_GUI_PROJECT_DIR': str(project_dir),
      'PI_GUI_SESSION_ROOT': str(output_dir / 'pi-runs'),
      'PI_GUI_THINKING': config.agent.thinking,
      'PI_GUI_LEARNING': '1' if config.agent.learning else '0',
      'PI_GUI_DISABLE_LEDGER_TOOL': '1' if config.agent.disable_ledger_tool else '0',
      'PI_GUI_TIMEOUT_SECONDS': str(config.agent.timeout_seconds),
      'PI_GUI_MAX_ACTIONS': str(config.agent.max_actions),
      'PI_GUI_MAX_STEPS': str(config.agent.max_steps),
      'PI_GUI_MAX_MODEL_TOKENS': str(config.agent.max_model_tokens),
      'PI_GUI_SETTLE_MS': str(config.agent.settle_ms),
      'PI_GUI_CONTAINER_MODE': '1',
      'PI_GUI_CONTAINER_PREFIX': prefix or config.mobileworld.name_prefix,
      'PI_GUI_BACKEND_START_PORT': str(config.mobileworld.backend_start_port),
  }
  if config.mobileworld.proxy_url:
    values.update(
        PI_GUI_PROXY_URL=config.mobileworld.proxy_url,
        PI_GUI_PROXY_RELAY_PORT=str(config.mobileworld.proxy_relay_port),
        HTTP_PROXY=config.mobileworld.proxy_url,
        HTTPS_PROXY=config.mobileworld.proxy_url,
        http_proxy=config.mobileworld.proxy_url,
        https_proxy=config.mobileworld.proxy_url,
    )
  if config.agent.provider and config.agent.model:
    values.update(PI_GUI_PROVIDER=config.agent.provider, PI_GUI_MODEL=config.agent.model)
  credentials = {}
  credential_file = credential_file or _credential_file(project_dir)
  if credential_file:
    credentials = {
        key: value for key, value in dotenv_values(credential_file).items()
        if value is not None
    }
  return {**credentials, **os.environ, **values}


def _start_proxy_relay(proxy_url: str | None, relay_port: int):
  """Expose a loopback-only host proxy to Docker workers during the run."""
  if not proxy_url:
    return None
  parsed = urlparse(proxy_url)
  if parsed.scheme != 'http' or not parsed.hostname or not parsed.port:
    raise ValueError('mobileworld.proxy_url must be an http://host:port URL')
  if parsed.hostname not in {'127.0.0.1', 'localhost'}:
    raise ValueError('mobileworld.proxy_url must refer to a host-local proxy')
  target = (parsed.hostname, parsed.port)
  try:
    probe = socket.create_connection(target, timeout=2)
    probe.close()
  except OSError as error:
    raise ConnectionError(
        f'MobileWorld proxy is not reachable at {proxy_url}'
    ) from error

  class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
      upstream = socket.create_connection(target, timeout=10)
      try:
        sockets = [self.request, upstream]
        while True:
          readable, _, _ = select.select(sockets, [], [], 30)
          if not readable:
            continue
          for source in readable:
            data = source.recv(65536)
            if not data:
              return
            destination = upstream if source is self.request else self.request
            destination.sendall(data)
      finally:
        upstream.close()

  class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

  server = RelayServer(('0.0.0.0', relay_port), RelayHandler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  return server


def _validate_runtime(source_dir: Path, image: str) -> None:
  if not (source_dir / 'src' / 'mobile_world').is_dir():
    raise FileNotFoundError(f'Not a MobileWorld checkout: {source_dir}')
  subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL)
  try:
    subprocess.run(
        ['docker', 'image', 'inspect', image], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
  except subprocess.CalledProcessError as error:
    raise FileNotFoundError(
        f'MobileWorld agent image {image!r} is missing; run '
        '`scripts/build-mobileworld-image.sh` first'
    ) from error


def _output_dir(config: MobileWorldConfig) -> Path:
  timestamp = dt.datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')
  root = Path(os.environ.get('PI_GUI_OUTPUT_ROOT', 'benchmark-results')).resolve()
  return root / f'{config.experiment.name}-{timestamp}'


def _container_names(prefix: str) -> list[str]:
  result = subprocess.run(
      ['docker', 'ps', '--filter', f'name=^{prefix}_', '--format', '{{.Names}}'],
      check=True, capture_output=True, text=True,
  )
  return sorted(line for line in result.stdout.splitlines() if line)


def _ready_container_names(
    prefix: str, image: str, count: int | None = None,
) -> list[str]:
  image_id = _image_id(image)
  ready = []
  names = (
      [f'{prefix}_{index}' for index in range(count)]
      if count is not None else _container_names(prefix)
  )
  for name in names:
    result = subprocess.run(
        ['docker', 'inspect', '--format', '{{.State.Health.Status}} {{.Image}}', name],
        check=False, capture_output=True, text=True,
    )
    if result.stdout.split() == ['healthy', image_id]:
      ready.append(name)
  return ready


def _wait_for_ready_containers(
    prefix: str, image: str, count: int, timeout_seconds: int = 600,
) -> list[str]:
  deadline = time.monotonic() + timeout_seconds
  while True:
    ready = _ready_container_names(prefix, image, count)
    if len(ready) == count or time.monotonic() >= deadline:
      return ready
    time.sleep(2)


def _remove_containers(prefix: str, count: int | None = None) -> None:
  names = (
      [f'{prefix}_{index}' for index in range(count)]
      if count is not None else _container_names(prefix)
  )
  if names:
    subprocess.run(
        ['docker', 'rm', '-f', *names], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _print_container_diagnostics(prefix: str, count: int) -> None:
  """Print actionable context when the upstream readiness wait fails."""
  for index in range(count):
    name = f'{prefix}_{index}'
    inspect = subprocess.run(
        [
            'docker', 'inspect', '--format',
            'status={{.State.Status}} health={{if .State.Health}}'
            '{{.State.Health.Status}}{{else}}none{{end}}', name,
        ],
        check=False, capture_output=True, text=True,
    )
    if inspect.returncode != 0:
      continue
    print(f'\n--- {name}: {inspect.stdout.strip()} ---', flush=True)
    health = subprocess.run(
        [
            'docker', 'inspect', '--format',
            '{{if .State.Health}}{{range .State.Health.Log}}'
            '{{.Output}}{{end}}{{end}}', name,
        ],
        check=False, capture_output=True, text=True,
    )
    if health.stdout.strip():
      print(f'Healthcheck:\n{health.stdout.strip()}', flush=True)
    logs = subprocess.run(
        ['docker', 'logs', '--tail', '80', name],
        check=False, capture_output=True, text=True,
    )
    output = (logs.stdout + logs.stderr).strip()
    if output:
      print(f'Container logs:\n{output}', flush=True)


def _summarize(output_dir: Path, expected_tasks: int | None) -> dict[str, Any]:
  scores = []
  for path in (output_dir / 'trajectories').glob('*/result.txt'):
    first = path.read_text(encoding='utf8').splitlines()[0]
    if first.startswith('score:'):
      scores.append(float(first.partition(':')[2].strip()))
  missing = max(expected_tasks - len(scores), 0) if expected_tasks is not None else 0
  return {
      'recorded_tasks': len(scores),
      'expected_tasks': expected_tasks,
      'missing_tasks': missing,
      'successful_tasks': sum(score > 0.99 for score in scores),
      'success_rate': sum(score > 0.99 for score in scores) / len(scores) if scores else None,
  }


def _image_id(image: str) -> str:
  result = subprocess.run(
      ['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
      check=True, capture_output=True, text=True,
  )
  return result.stdout.strip()


def _credential_file(project_dir: Path) -> Path | None:
  configured = os.environ.get('PI_GUI_ENV_FILE')
  path = Path(configured).resolve() if configured else project_dir / '.env'
  return path if path.is_file() else None


def _write_json(path: Path, value: Any) -> None:
  temporary = path.with_suffix(path.suffix + '.tmp')
  temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
  temporary.replace(path)


if __name__ == '__main__':
  main()
