from __future__ import annotations

import argparse
import importlib
import sys
import types
import unittest
from unittest import mock


def _module(name: str, **values):
  module = types.ModuleType(name)
  for key, value in values.items():
    setattr(module, key, value)
  return module


class AndroidWorldRunTest(unittest.TestCase):

  def test_restores_accessibility_route_after_device_setup_before_tasks(self):
    adb_utils = _module('android_world.env.adb_utils', set_root_if_needed=lambda _: None)
    env_launcher = _module('android_world.env.env_launcher')
    android_tools = _module(
        'android_world.env.tools', AndroidToolController=mock.Mock(),
    )
    controller_calls = []

    class AdbController:
      def __init__(self):
        self._config = types.SimpleNamespace(default_timeout=5.0)

      def execute_command(self, args, timeout=None, device_specific=True):
        controller_calls.append((args, timeout, device_specific))
        return b''

    adb_controller = _module(
        'android_env.components.adb_controller', AdbController=AdbController,
    )
    wrapper = _module(
        'android_env.wrappers.a11y_grpc_wrapper',
        A11yGrpcWrapper=type('A11yGrpcWrapper', (), {}),
    )
    setup_device = _module(
        'android_world.env.setup_device.setup', setup_app=lambda *_, **__: None,
    )
    modules = {
        'android_world': _module('android_world'),
        'android_world.checkpointer': _module('android_world.checkpointer'),
        'android_world.registry': _module('android_world.registry'),
        'android_world.suite_utils': _module('android_world.suite_utils'),
        'android_world.env': _module(
            'android_world.env', adb_utils=adb_utils, env_launcher=env_launcher,
            tools=android_tools,
        ),
        'android_world.env.adb_utils': adb_utils,
        'android_world.env.env_launcher': env_launcher,
        'android_world.env.tools': android_tools,
        'android_world.env.setup_device': _module(
            'android_world.env.setup_device', setup=setup_device,
        ),
        'android_world.env.setup_device.setup': setup_device,
        'android_world.env.setup_device.apps': _module(
            'android_world.env.setup_device.apps', download_app_data=lambda _: '',
        ),
        'android_world.utils': _module('android_world.utils'),
        'android_world.utils.app_snapshot': _module(
            'android_world.utils.app_snapshot', save_snapshot=mock.Mock(),
        ),
        'android_world.utils.file_utils': _module(
            'android_world.utils.file_utils', check_file_exists=lambda *_, **__: True,
        ),
        'android_env': _module('android_env'),
        'android_env.components': _module(
            'android_env.components', adb_controller=adb_controller,
        ),
        'android_env.components.adb_controller': adb_controller,
        'android_env.wrappers': _module(
            'android_env.wrappers', a11y_grpc_wrapper=wrapper,
        ),
        'android_env.wrappers.a11y_grpc_wrapper': wrapper,
        'experiments.androidworld.factory': _module(
            'experiments.androidworld.factory', create_agent=lambda *_, **__: object(),
        ),
    }
    modules['android_world'].checkpointer = modules['android_world.checkpointer']
    modules['android_world'].registry = modules['android_world.registry']
    modules['android_world'].suite_utils = modules['android_world.suite_utils']
    modules['android_world.env.setup_device'].apps = modules[
        'android_world.env.setup_device.apps'
    ]
    modules['android_world.utils'].file_utils = modules[
        'android_world.utils.file_utils'
    ]
    modules['android_world.utils'].app_snapshot = modules[
        'android_world.utils.app_snapshot'
    ]

    with mock.patch.dict(sys.modules, modules):
      sys.modules.pop('experiments.androidworld.run', None)
      runner = importlib.import_module('experiments.androidworld.run')
      runner._increase_activity_start_timeout()
      controller = AdbController()
      controller.execute_command(['shell', 'am', 'start', '-W', '-n', 'pkg/.A'])
      controller.execute_command(['shell', 'pm', 'list', 'packages'])
      self.assertEqual(controller_calls[0][1], 15.0)
      self.assertIsNone(controller_calls[1][1])
      vlc_controller = mock.Mock()
      runner.tools.AndroidToolController.return_value = vlc_controller
      runner.file_utils.check_file_exists = mock.Mock(side_effect=[False, True])
      runner.adb_utils.get_adb_activity = mock.Mock(return_value='activity')
      runner.adb_utils.extract_package_name = mock.Mock(return_value='org.videolan.vlc')
      runner.adb_utils.issue_generic_request = mock.Mock()
      runner.adb_utils.close_app = mock.Mock()
      runner.time.sleep = mock.Mock()
      vlc_env = mock.Mock(controller='device')
      vlc_env.get_state.return_value = types.SimpleNamespace(
          ui_elements=[types.SimpleNamespace(text='SKIP')],
      )
      runner._ensure_vlc_database(vlc_env)
      clicked = [
          call.args[0] for call in vlc_controller.click_element.call_args_list
      ]
      self.assertEqual(clicked, ['SKIP'])
      runner.adb_utils.close_app.assert_called_once_with('vlc', 'device')
      runner.app_snapshot.save_snapshot.assert_called_once_with('vlc', 'device')
      events = []
      restore = mock.Mock(side_effect=lambda: events.append('restore'))
      environment = mock.Mock()
      environment.close = mock.Mock()
      config = types.SimpleNamespace(
          runtime=types.SimpleNamespace(
              adb_path='adb', console_port=5554, grpc_port=8554,
              workspace_dir='.', session_dir='/output/runs',
              checkpoint_dir='/output/checkpoints',
          ),
          suite=types.SimpleNamespace(
              setup_mode='never', family='android_world', combinations=1,
              seed=30, tasks=('Task',), fixed_task_seed=False,
              timeout_seconds=10, settle_ms=0, action_budget_multiplier=1,
              min_actions=1, max_model_tokens=10,
          ),
          agent=types.SimpleNamespace(
              name='pi-gui', thinking='low', learning=False, provider=None,
              model=None, openclaw_model='mimo', enable_ledger_tool=False,
              disable_ledger_tool=False,
          ),
      )
      runner.parse_args = lambda: argparse.Namespace(config='worker.json')
      runner.load_worker_config = lambda _: config
      runner._configure_download_cache = mock.Mock()
      runner._stabilize_camera_setup = mock.Mock()
      runner._stabilize_chrome_setup = mock.Mock()
      runner._stabilize_clipper_setup = mock.Mock()
      runner._stabilize_contacts_setup = mock.Mock()
      runner._stabilize_simple_sms_setup = mock.Mock()
      runner._stabilize_vlc_setup = mock.Mock()
      runner._stabilize_sms_reads = mock.Mock()
      runner._route_accessibility_over_adb = mock.Mock(return_value=restore)
      runner.env_launcher.load_and_setup_env = mock.Mock(
          side_effect=lambda **_: events.append('setup') or environment,
      )
      registry_instance = mock.Mock()
      registry_instance.get_registry.return_value = {}
      runner.registry.TaskRegistry = mock.Mock(return_value=registry_instance)
      suite = types.SimpleNamespace(suite_family=None)
      runner.suite_utils.create_suite = mock.Mock(return_value=suite)
      runner.create_agent = mock.Mock(return_value=object())
      runner.checkpointer.IncrementalCheckpointer = mock.Mock(return_value=object())
      runner.suite_utils.run = mock.Mock(
          side_effect=lambda *_, **__: events.append('run') or [],
      )
      runner.suite_utils.process_episodes = mock.Mock()

      runner.main()

      self.assertEqual(events, ['setup', 'restore', 'run'])
      environment.close.assert_called_once_with()

      modules['android_world'].constants = _module('android_world.constants')
      sys.modules['android_world.constants'] = modules['android_world'].constants
      sys.modules.pop('experiments.androidworld.http_run', None)
      http_runner = importlib.import_module('experiments.androidworld.http_run')
      http_runner._increase_activity_start_timeout = mock.Mock()
      http_runner._configure_download_cache = mock.Mock()
      http_runner._stabilize_camera_setup = mock.Mock()
      http_runner._stabilize_chrome_setup = mock.Mock()
      http_runner._stabilize_clipper_setup = mock.Mock()
      http_runner._stabilize_contacts_setup = mock.Mock()
      http_runner._stabilize_simple_sms_setup = mock.Mock()
      http_runner._stabilize_vlc_setup = mock.Mock()
      http_environment = mock.Mock()
      http_runner.env_launcher.load_and_setup_env = mock.Mock(
          return_value=http_environment,
      )

      http_runner._prepare_device(config)

      http_runner.env_launcher.load_and_setup_env.assert_called_once_with(
          console_port=5554, emulator_setup=True, adb_path='adb', grpc_port=8554,
      )
      http_environment.close.assert_called_once_with()
      sys.modules.pop('experiments.androidworld.http_run', None)
    sys.modules.pop('experiments.androidworld.run', None)


if __name__ == '__main__':
  unittest.main()
