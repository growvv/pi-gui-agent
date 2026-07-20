from pathlib import Path
from dataclasses import replace
import io
import json
import tempfile
import unittest
from unittest import mock

from experiments.androidworld.config import load_config
from experiments.androidworld.parallel import (
    _balanced_shards,
    _docker_command,
    _ensure_worker_network,
    _wait_for_start_capacity,
    _prepare_workers,
    _relay_worker_output,
)


class ParallelRunnerTest(unittest.TestCase):

  def test_waits_for_cpu_to_be_stably_below_startup_threshold(self):
    settings = mock.Mock(
        worker_start_interval_seconds=0,
        startup_cpu_max_percent=30,
        startup_cpu_stable_samples=2,
        startup_cpu_timeout_seconds=60,
    )
    with (
        mock.patch(
            'experiments.androidworld.parallel._host_cpu_percent',
            side_effect=[80, 20, 40, 20, 10],
        ) as cpu,
        mock.patch('experiments.androidworld.parallel.time.sleep'),
        mock.patch('experiments.androidworld.parallel.time.monotonic', side_effect=range(20)),
    ):
      _wait_for_start_capacity(settings, 2)

    self.assertEqual(cpu.call_count, 5)

  def test_skips_cpu_wait_when_throttling_is_disabled(self):
    settings = mock.Mock(
        worker_start_interval_seconds=0,
        startup_cpu_max_percent=None,
    )
    with mock.patch('experiments.androidworld.parallel._host_cpu_percent') as cpu:
      _wait_for_start_capacity(settings, 2)
    cpu.assert_not_called()

  def test_relays_worker_output_to_log_and_terminal(self):
    stream = io.StringIO('booting\nrunning\n')
    log = io.StringIO()
    terminal = io.StringIO()
    with mock.patch('sys.stdout', terminal):
      _relay_worker_output(2, stream, log)
    self.assertEqual(log.getvalue(), 'booting\nrunning\n')
    self.assertEqual(
        terminal.getvalue(), '[worker-2] booting\n[worker-2] running\n',
    )

  def test_balances_complex_tasks_first(self):
    shards = _balanced_shards({'large': 10, 'a': 3, 'b': 3, 'c': 3}, 2)
    totals = [sum({'large': 10, 'a': 3, 'b': 3, 'c': 3}[task] for task in shard)
              for shard in shards]
    self.assertEqual(sorted(totals), [9, 10])

  def test_container_receives_only_worker_config_argument(self):
    config = load_config('configs/androidworld/main.toml')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / '.env').write_text(
          'XIAOMI_TOKEN_PLAN_CN_API_KEY=test\n', encoding='utf8',
      )
      worker = root / 'worker-1'
      worker.mkdir()
      command = _docker_command(
          config, root, worker, worker / 'config.json', 'worker-1',
          create_cache=False,
      )
    module_index = command.index('experiments.androidworld.run')
    self.assertEqual(command[module_index - 1], '-m')
    self.assertEqual(command[module_index + 1], '/output/config.json')
    self.assertNotIn('--thinking', command)
    self.assertNotIn('--tasks', command)
    self.assertIn('ANDROID_WORLD_DOWNLOAD_CACHE_DIR=/download-cache', command)
    network_index = command.index('--network')
    self.assertEqual(command[network_index + 1], 'pi-gui-androidworld-ipv6')
    self.assertNotIn('/root/.android/avd', command)

  def test_creates_ipv6_network_for_emulator_modem(self):
    created_network = '[{"EnableIPv6":true,"IPAM":{"Config":[' \
        '{"Subnet":"fd42:7069:6775::/64"}]}}]'
    missing = mock.Mock(returncode=1, stdout='', stderr='not found')
    created = mock.Mock(returncode=0, stdout='network-id\n', stderr='')
    inspected = mock.Mock(returncode=0, stdout=created_network, stderr='')
    with mock.patch(
        'experiments.androidworld.parallel.subprocess.run',
        side_effect=[missing, created, inspected],
    ) as run:
      self.assertEqual(_ensure_worker_network(), 'pi-gui-androidworld-ipv6')
    self.assertEqual(
        run.call_args_list[1].args[0],
        [
            'docker', 'network', 'create', '--driver', 'bridge', '--ipv6',
            '--subnet', 'fd42:7069:6775::/64', 'pi-gui-androidworld-ipv6',
        ],
    )

  def test_rejects_existing_network_without_ipv6(self):
    inspected = mock.Mock(
        returncode=0,
        stdout='[{"EnableIPv6":false,"IPAM":{"Config":[]}}]', stderr='',
    )
    with mock.patch(
        'experiments.androidworld.parallel.subprocess.run',
        return_value=inspected,
    ):
      with self.assertRaisesRegex(RuntimeError, 'must have IPv6 enabled'):
        _ensure_worker_network()

  def test_fastapi_transport_selects_http_runner(self):
    config = load_config('configs/androidworld/fastapi-smoke.toml')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / '.env').write_text(
          'XIAOMI_TOKEN_PLAN_CN_API_KEY=test\n', encoding='utf8',
      )
      worker = root / 'worker-1'
      worker.mkdir()
      command = _docker_command(
          config, root, worker, worker / 'config.json', 'worker-1',
          create_cache=False,
      )
    self.assertIn('experiments.androidworld.http_run', command)
    self.assertNotIn('experiments.androidworld.run', command)

  def test_keep_containers_omits_rm_and_uses_run_specific_names(self):
    config = load_config('configs/androidworld/main.toml')
    config = replace(
        config, container=replace(config.container, keep_containers=True),
    )
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / '.env').write_text(
          'XIAOMI_TOKEN_PLAN_CN_API_KEY=test\n', encoding='utf8',
      )
      workers = _prepare_workers(
          config, root, root / 'run+0800', [['Task']], write=False,
      )
    command = workers[0]['command']
    self.assertNotIn('--rm', command)
    self.assertEqual(
        workers[0]['container_name'],
        'pi-gui-androidworld-run-0800-1',
    )

  def test_missing_forwarded_credential_fails_before_container_start(self):
    config = load_config('configs/androidworld/main.toml')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / '.env').write_text('UNRELATED=test\n', encoding='utf8')
      worker = root / 'worker-1'
      worker.mkdir()
      with self.assertRaisesRegex(ValueError, 'XIAOMI_TOKEN_PLAN_CN_API_KEY'):
        _docker_command(
            config, root, worker, worker / 'config.json', 'worker-1',
            create_cache=False,
        )

  def test_openclaw_receives_an_isolated_writable_config(self):
    config = load_config('configs/androidworld/baseline-openclaw.toml')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / '.env').write_text(
          'XIAOMI_TOKEN_PLAN_CN_API_KEY=test\n', encoding='utf8',
      )
      worker = root / 'worker-1'
      config_dir = worker / '.openclaw'
      config_dir.mkdir(parents=True)
      command = _docker_command(
          config, root, worker, worker / 'config.json', 'worker-1',
          create_cache=False, agent_config_dir=config_dir,
      )
    self.assertIn(f'{config_dir}:/home/agent/.openclaw:rw', command)

  def test_prepares_agent_output_directories_before_container_start(self):
    config = load_config('configs/androidworld/main.toml')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / '.env').write_text(
          'XIAOMI_TOKEN_PLAN_CN_API_KEY=test\n', encoding='utf8',
      )
      workers = _prepare_workers(
          config, root, root / 'results', [['Task']], write=True,
      )
      worker_dir = workers[0]['worker_dir']
      for directory in ('checkpoints', 'ledgers', 'runs', 'learning'):
        path = worker_dir / directory
        self.assertTrue(path.is_dir())
        self.assertEqual(path.stat().st_uid, root.stat().st_uid)
      state = json.loads((worker_dir / 'state.json').read_text(encoding='utf8'))
      self.assertEqual(state['status'], 'pending')
      self.assertEqual(state['max_steps'], 100)
      self.assertEqual(state['tasks'][0]['task_name'], 'Task')


if __name__ == '__main__':
  unittest.main()
