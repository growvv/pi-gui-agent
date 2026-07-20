from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


mobile_world = types.ModuleType('mobile_world')
agents = types.ModuleType('mobile_world.agents')
base = types.ModuleType('mobile_world.agents.base')
runtime = types.ModuleType('mobile_world.runtime')
utils = types.ModuleType('mobile_world.runtime.utils')
models = types.ModuleType('mobile_world.runtime.utils.models')


class _BaseAgent:
  def __init__(self, *args, **kwargs):
    del args, kwargs


class _JSONAction:
  def __init__(self, action_type=None):
    self.action_type = action_type


base.BaseAgent = _BaseAgent
models.FINISHED = 'finished'
models.UNKNOWN = 'unknown'
models.JSONAction = _JSONAction
sys.modules.setdefault('mobile_world', mobile_world)
sys.modules.setdefault('mobile_world.agents', agents)
sys.modules.setdefault('mobile_world.agents.base', base)
sys.modules.setdefault('mobile_world.runtime', runtime)
sys.modules.setdefault('mobile_world.runtime.utils', utils)
sys.modules.setdefault('mobile_world.runtime.utils.models', models)

from experiments.mobileworld.agent import (  # noqa: E402
    MOBILEWORLD_APPS,
    MATTERMOST_LOGIN_HINT,
    PiGuiMobileWorldAgent,
    resolve_container,
    _docker_gateway,
)
from experiments.mobileworld.config import load_config  # noqa: E402
from experiments.mobileworld.run import (  # noqa: E402
    _agent_environment,
    _environment_command,
    _evaluation_command,
    _ready_container_names,
)


class MobileWorldConfigTest(unittest.TestCase):

  def test_workers_drive_environment_and_evaluator_concurrency(self):
    config = load_config('configs/mobileworld/main.toml')
    project = Path('/project')
    source = Path('/mobileworld')
    output = Path('/output')
    environment = _environment_command(config, source, 'run-prefix', project)
    evaluation = _evaluation_command(
        config, source, output, 'run-prefix', project,
    )
    expected_workers = str(config.experiment.workers)
    self.assertEqual(environment[environment.index('--count') + 1], expected_workers)
    self.assertEqual(
        evaluation[evaluation.index('--max-concurrency') + 1], expected_workers,
    )
    self.assertEqual(evaluation[evaluation.index('--max-round') + 1], '1')
    self.assertEqual(
        evaluation[evaluation.index('--aw-host') + 1],
        ','.join(
            f'http://localhost:{16800 + index}'
            for index in range(config.experiment.workers)
        ),
    )
    self.assertIn('--no-sync', environment)
    self.assertIn('--no-sync', evaluation)
    self.assertIn('/project/experiments/mobileworld/agent.py', evaluation)
    self.assertTrue(config.mobileworld.reuse_containers)
    self.assertTrue(config.mobileworld.keep_containers)
    self.assertEqual(config.mobileworld.startup_timeout_seconds, 900)
    self.assertFalse(hasattr(config.mobileworld, 'source_dir'))
    self.assertFalse(hasattr(config.experiment, 'output_root'))

  def test_smoke_selects_configured_model(self):
    config = load_config('configs/mobileworld/smoke.toml')
    self.assertEqual(config.agent.provider, 'xiaomi-token-plan-cn')
    self.assertEqual(config.agent.model, 'mimo-v2.5')

  def test_small_config_routes_android_traffic_through_host_proxy(self):
    config = load_config('configs/mobileworld/small.toml')
    self.assertEqual(config.mobileworld.proxy_url, 'http://127.0.0.1:7892')
    with mock.patch.dict('os.environ', {}, clear=True):
      environment = _agent_environment(config, Path('/project'), Path('/output'))
    self.assertEqual(environment['HTTP_PROXY'], config.mobileworld.proxy_url)
    self.assertEqual(environment['PI_GUI_PROXY_RELAY_PORT'], '17892')

  @mock.patch('experiments.mobileworld.run.subprocess.run')
  def test_reuses_only_healthy_containers_from_current_image(self, run):
    image_id = 'sha256:current'
    run.side_effect = [
        mock.Mock(stdout=f'{image_id}\n'),
        mock.Mock(stdout='worker-1\nworker-2\nworker-old\n'),
        mock.Mock(stdout=f'healthy {image_id}\n'),
        mock.Mock(stdout=f'unhealthy {image_id}\n'),
        mock.Mock(stdout='healthy sha256:old\n'),
    ]
    self.assertEqual(
        _ready_container_names('workers', 'agent-image'), ['worker-1'],
    )

  def test_rejects_unknown_keys(self):
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'bad.toml'
      path.write_text(
          '[experiment]\nname="bad"\nworkers=2\n'
          '[mobileworld]\nlaunch_intervall=1\n', encoding='utf8',
      )
      with self.assertRaisesRegex(ValueError, 'launch_intervall'):
        load_config(path)

  def test_env_file_credentials_reach_host_agent(self):
    config = load_config('configs/mobileworld/main.toml')
    with tempfile.TemporaryDirectory() as temporary:
      project = Path(temporary)
      (project / '.env').write_text('MOBILEWORLD_TEST_TOKEN=from-file\n', encoding='utf8')
      with mock.patch.dict('os.environ', {}, clear=True):
        environment = _agent_environment(config, project, project / 'output')
    self.assertEqual(environment['MOBILEWORLD_TEST_TOKEN'], 'from-file')
    self.assertEqual(environment['PI_GUI_PROJECT_DIR'], str(project))


class MobileWorldAgentTest(unittest.TestCase):

  @mock.patch('experiments.mobileworld.agent._copy_from_container')
  @mock.patch('experiments.mobileworld.agent._configure_android_proxy')
  @mock.patch('experiments.mobileworld.agent.resolve_container', return_value='worker')
  @mock.patch('experiments.mobileworld.agent.subprocess.run')
  def test_explicit_answer_reaches_evaluator_cache(
      self, run, _resolve, configure_proxy, copy_from_container,
  ):
    del copy_from_container
    run.return_value = mock.Mock(returncode=0, stdout='', stderr='')
    configure_proxy.return_value = mock.Mock()
    env = types.SimpleNamespace(base_url='http://localhost:16800')
    agent = PiGuiMobileWorldAgent(env=env)
    agent.instruction = 'Answer the question'
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      result_dir = root / 'pi-runs'

      def write_result(_container, _source, destination):
        (destination / 'result.json').write_text(
            '{"finished": true, "answer": "42", "actions": 3}', encoding='utf8',
        )
      with mock.patch(
          'experiments.mobileworld.agent._copy_from_container', side_effect=write_result,
      ), mock.patch.dict('os.environ', {
          'PI_GUI_PROJECT_DIR': str(root),
          'PI_GUI_SESSION_ROOT': str(result_dir),
      }, clear=True):
        _prediction, action = agent.predict({})
    self.assertEqual(action.action_type, 'finished')
    self.assertEqual(env.interaction_cache, '42')

  def test_mobileworld_app_map_covers_official_apps(self):
    expected = {
        'calendar', 'camera', 'chrome', 'clock', 'contacts', 'files', 'gallery',
        'mail', 'maps', 'mastodon', 'mattermost', 'messages', 'settings', 'sms',
        'taodian', '桌面', '淘店', '设置',
    }
    self.assertEqual(set(MOBILEWORLD_APPS), expected)

  def test_mattermost_hint_uses_official_local_benchmark_account(self):
    self.assertIn('sam.oneill@neuralforge.ai', MATTERMOST_LOGIN_HINT)
    self.assertIn('password password', MATTERMOST_LOGIN_HINT)

  @mock.patch('experiments.mobileworld.agent.subprocess.run')
  def test_proxy_gateway_comes_from_docker_inspect(self, run):
    run.return_value = mock.Mock(returncode=0, stdout='172.17.0.1\n')
    self.assertEqual(_docker_gateway('worker'), '172.17.0.1')
    run.assert_called_once_with(
        ['docker', 'inspect', '--format',
         '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}', 'worker'],
        check=False, capture_output=True, text=True,
    )

  @mock.patch('experiments.mobileworld.agent.subprocess.run')
  def test_known_prefix_resolves_without_global_container_scan(self, run):
    run.return_value = mock.Mock(
        returncode=0,
        stdout=json.dumps([{'NetworkSettings': {'Ports': {
            '6800/tcp': [{'HostPort': '16801'}],
        }}}]),
    )
    environment = {
        'PI_GUI_CONTAINER_PREFIX': 'workers',
        'PI_GUI_BACKEND_START_PORT': '16800',
    }
    with mock.patch.dict('os.environ', environment, clear=True):
      container = resolve_container('http://localhost:16801')
    self.assertEqual(container, 'workers_1')
    run.assert_called_once_with(
        ['docker', 'inspect', 'workers_1'], check=False,
        capture_output=True, text=True,
    )

  @mock.patch('experiments.mobileworld.agent.subprocess.run')
  def test_backend_port_selects_its_own_container(self, run):
    run.side_effect = [
        mock.Mock(stdout='first second\n'),
        mock.Mock(stdout=json.dumps([
            {'Name': '/first', 'NetworkSettings': {'Ports': {
                '6800/tcp': [{'HostPort': '16800'}],
                '5556/tcp': [{'HostPort': '15556'}],
            }}},
            {'Name': '/second', 'NetworkSettings': {'Ports': {
                '6800/tcp': [{'HostPort': '16801'}],
                '5556/tcp': [{'HostPort': '15557'}],
            }}},
        ])),
    ]
    with mock.patch.dict('os.environ', {}, clear=True):
      container = resolve_container('http://localhost:16801')
    self.assertEqual(container, 'second')


if __name__ == '__main__':
  unittest.main()
