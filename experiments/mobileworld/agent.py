"""MobileWorld dynamic-agent adapter for the pi-gui command-line agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any
from urllib.parse import urlparse
import uuid

from mobile_world.agents.base import BaseAgent
from mobile_world.runtime.utils.models import FINISHED, UNKNOWN, JSONAction


MOBILEWORLD_APPS = {
    '桌面': 'com.google.android.apps.nexuslauncher',
    'calendar': 'org.fossify.calendar',
    'camera': 'com.android.camera2',
    'chrome': 'com.android.chrome',
    'clock': 'com.google.android.deskclock',
    'contacts': 'com.google.android.contacts',
    'files': 'com.google.android.documentsui',
    'gallery': 'gallery.photomanager.picturegalleryapp.imagegallery',
    'mail': 'com.gmailclone',
    'maps': 'com.google.android.apps.maps',
    'mastodon': 'org.joinmastodon.android.mastodon',
    'mattermost': 'com.mattermost.rnbeta',
    'messages': 'com.google.android.apps.messaging',
    'settings': 'com.android.settings',
    '设置': 'com.android.settings',
    'sms': 'com.google.android.apps.messaging',
    'taodian': 'com.testmall.app',
    '淘店': 'com.testmall.app',
}

MATTERMOST_LOGIN_HINT = (
    '\n\nThis MobileWorld task uses the local benchmark Mattermost service. If the app '
    'shows a login screen, connect to the prefilled server and sign in with the '
    'benchmark account sam.oneill@neuralforge.ai and password password. These '
    'credentials are explicitly provided for this local test service.'
)


class PiGuiMobileWorldAgent(BaseAgent):
  """Runs one complete pi-gui session, then lets MobileWorld score the device."""

  def __init__(self, *args: Any, env: Any, **kwargs: Any) -> None:
    super().__init__(*args, **kwargs)
    self.env = env
    self._ran = False

  def predict(self, observation: dict[str, Any]) -> tuple[str, JSONAction]:
    del observation
    if self._ran:
      return 'pi-gui was already run for this task', JSONAction(action_type=UNKNOWN)
    self._ran = True

    root = Path(_required_env('PI_GUI_PROJECT_DIR')).resolve()
    session_root = Path(_required_env('PI_GUI_SESSION_ROOT')).resolve()
    session_dir = session_root / uuid.uuid4().hex
    session_dir.mkdir(parents=True, exist_ok=False)
    container = resolve_container(self.env.base_url)
    container_root = os.environ.get('PI_GUI_CONTAINER_ROOT', '/opt/pi-gui-agent')
    container_session = f'/tmp/pi-gui-mobileworld/{session_dir.name}'
    result_file = Path(container_session, 'result.json')
    adb = os.environ.get('PI_GUI_ADB', '/opt/android-sdk/platform-tools/adb')

    command = [
        os.environ.get('PI_GUI_NODE', '/usr/bin/node'),
        f'{container_root}/dist/cli.js',
        '--adb', adb, '--serial', 'emulator-5554',
        '--thinking', os.environ.get('PI_GUI_THINKING', 'medium'),
        '--max-actions', os.environ.get('PI_GUI_MAX_ACTIONS', '50'),
        '--max-model-tokens', os.environ.get('PI_GUI_MAX_MODEL_TOKENS', '4096'),
        '--settle-ms', os.environ.get('PI_GUI_SETTLE_MS', '1500'),
        '--result-file', str(result_file), '--session-dir', container_session,
        '--ledger-dir', f'{container_session}/ledgers',
        '--learning-root', f'{container_session}/learning',
        '--app-map', json.dumps(MOBILEWORLD_APPS, ensure_ascii=False),
    ]
    provider = os.environ.get('PI_GUI_PROVIDER')
    model = os.environ.get('PI_GUI_MODEL')
    if provider and model:
      command.extend(['--provider', provider, '--model', model])
    if os.environ.get('PI_GUI_LEARNING', '0') != '1':
      command.append('--no-learning')
    instruction = (
        self.instruction + '\n\nMobileWorld app names are configured for open_app; '
        'use the friendly names in the task directly.'
    )
    if 'mattermost' in self.instruction.lower():
      instruction += MATTERMOST_LOGIN_HINT
    command.append(instruction)

    try:
      proxy_cleanup = _configure_android_proxy(container, adb)
      shell_command = 'set -a; [ ! -f /app/service/.env ] || . /app/service/.env; '
      if os.environ.get('PI_GUI_PROXY_URL'):
        worker_proxy = 'http://127.0.0.1:17893'
        shell_command += (
            f'export HTTP_PROXY={worker_proxy} HTTPS_PROXY={worker_proxy} '
            f'http_proxy={worker_proxy} https_proxy={worker_proxy}; '
        )
      shell_command += 'exec ' + ' '.join(shlex.quote(value) for value in command)
      exec_command = ['docker', 'exec', container, 'sh', '-lc', shell_command]
      result = subprocess.run(
          exec_command, cwd=root, env=os.environ.copy(), capture_output=True,
          text=True, timeout=int(os.environ.get('PI_GUI_TIMEOUT_SECONDS', '1800')),
          check=False,
      )
      _copy_from_container(container, result_file.parent, session_dir)
      payload = _read_result(session_dir / 'result.json')
      answer = payload.get('answer')
      if isinstance(answer, str):
        self.env.interaction_cache = answer
      finished = result.returncode == 0 and payload.get('finished') is True
      summary = {
          'finished': finished,
          'returncode': result.returncode,
          'actions': payload.get('actions', 0),
          'answer': answer,
          'stdout': result.stdout[-4000:],
          'stderr': result.stderr[-4000:],
      }
    except subprocess.TimeoutExpired as error:
      summary = {
          'finished': False, 'returncode': None,
          'error': f'pi-gui timed out: {error}',
      }
      finished = False
    finally:
      if 'proxy_cleanup' in locals():
        proxy_cleanup()
    (session_dir / 'mobileworld-result.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8',
    )
    action = FINISHED if finished else UNKNOWN
    return json.dumps(summary, ensure_ascii=False), JSONAction(action_type=action)

  def reset(self) -> None:
    self._ran = False


def resolve_container(base_url: str) -> str:
  """Find the MobileWorld container belonging to a backend URL."""
  backend_port = urlparse(base_url).port
  if backend_port is None:
    raise RuntimeError(f'MobileWorld backend URL has no port: {base_url}')
  prefix = os.environ.get('PI_GUI_CONTAINER_PREFIX')
  start_port = os.environ.get('PI_GUI_BACKEND_START_PORT')
  if prefix and start_port:
    index = backend_port - int(start_port)
    if index >= 0:
      name = f'{prefix}_{index}'
      inspected = subprocess.run(
          ['docker', 'inspect', name], check=False, capture_output=True, text=True,
      )
      if inspected.returncode == 0:
        values = json.loads(inspected.stdout)
        if values and _container_backend_port(values[0]) == backend_port:
          return name
  ids = subprocess.run(
      ['docker', 'ps', '-q'], check=True, capture_output=True, text=True,
  ).stdout.split()
  if not ids:
    raise RuntimeError('No running Docker containers found for MobileWorld')
  inspected = subprocess.run(
      ['docker', 'inspect', *ids], check=True, capture_output=True, text=True,
  )
  for container in json.loads(inspected.stdout):
    backend = _container_backend_port(container)
    if backend == backend_port:
      name = container.get('Name', '').lstrip('/')
      if name:
        return name
  raise RuntimeError(f'No MobileWorld container exposes backend port {backend_port}')


def _container_backend_port(container: dict[str, Any]) -> int | None:
  ports = container.get('NetworkSettings', {}).get('Ports', {})
  return _host_port(ports.get('6800/tcp'))


def _host_port(bindings: Any) -> int | None:
  if not bindings:
    return None
  try:
    return int(bindings[0]['HostPort'])
  except (KeyError, TypeError, ValueError):
    return None


def _copy_from_container(container: str, source: Path, destination: Path) -> None:
  subprocess.run(
      ['docker', 'cp', f'{container}:{source}/.', str(destination)],
      check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
  )


def _read_result(path: Path) -> dict[str, Any]:
  try:
    value = json.loads(path.read_text(encoding='utf8'))
  except (FileNotFoundError, json.JSONDecodeError):
    return {}
  return value if isinstance(value, dict) else {}


def _configure_android_proxy(container: str, adb: str):
  """Route Android HTTP traffic through the host-side benchmark relay."""
  proxy_url = os.environ.get('PI_GUI_PROXY_URL')
  relay_port = os.environ.get('PI_GUI_PROXY_RELAY_PORT')
  if not proxy_url or not relay_port:
    return lambda: None
  parsed = urlparse(proxy_url)
  if parsed.hostname not in {'127.0.0.1', 'localhost'}:
    raise ValueError('MobileWorld proxy_url currently requires a host-local proxy')
  script = (
      "const n=require('net'),p=process.argv.slice(1);"
      "n.createServer(c=>{const u=n.connect(+p[1],p[0]);"
      "c.pipe(u).pipe(c);u.on('error',()=>c.destroy())}).listen(+p[2],'127.0.0.1')"
  )
  gateway = _docker_gateway(container)
  if not gateway:
    raise RuntimeError(f'Cannot determine Docker gateway for {container}')
  worker_port = '17893'
  start = subprocess.run(
      ['docker', 'exec', '-d', container, '/usr/local/bin/node', '-e', script,
       gateway, relay_port, worker_port], check=False,
  )
  if start.returncode != 0:
    raise RuntimeError(f'Cannot start proxy relay in {container}')
  adb_prefix = ['docker', 'exec', container, adb, '-s', 'emulator-5554']
  subprocess.run(
      [*adb_prefix, 'reverse', f'tcp:{worker_port}', f'tcp:{worker_port}'],
      check=True, capture_output=True,
  )
  subprocess.run(
      [*adb_prefix, 'shell', 'settings', 'put', 'global', 'http_proxy',
       f'127.0.0.1:{worker_port}'], check=True, capture_output=True,
  )

  def cleanup() -> None:
    subprocess.run(
        [*adb_prefix, 'shell', 'settings', 'put', 'global', 'http_proxy', ':0'],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [*adb_prefix, 'reverse', '--remove', f'tcp:{worker_port}'],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
  return cleanup


def _docker_gateway(container: str) -> str:
  """Resolve a worker's bridge gateway without requiring iproute in the image."""
  inspected = subprocess.run(
      ['docker', 'inspect', '--format',
       '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}', container],
      check=False, capture_output=True, text=True,
  )
  gateway = inspected.stdout.strip()
  if gateway:
    return gateway
  # Older/custom Docker setups may omit Gateway from inspect output.
  fallback = subprocess.run(
      ['docker', 'exec', container, 'sh', '-lc',
       "command -v ip >/dev/null 2>&1 && ip route | awk '/default/ {print $3; exit}' || true"],
      check=False, capture_output=True, text=True,
  )
  return fallback.stdout.strip()


def _required_env(name: str) -> str:
  value = os.environ.get(name)
  if not value:
    raise RuntimeError(f'{name} is required')
  return value
