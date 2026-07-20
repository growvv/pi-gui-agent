from pathlib import Path
from dataclasses import replace
import io
import tempfile
import unittest
from unittest import mock

from experiments.androidworld.config import load_config
from experiments.androidworld.parallel import (
    _balanced_shards,
    _docker_command,
    _prepare_workers,
    _relay_worker_output,
)


class ParallelRunnerTest(unittest.TestCase):

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
    self.assertNotIn('/root/.android/avd', command)

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


if __name__ == '__main__':
  unittest.main()
