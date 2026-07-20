from pathlib import Path
import tempfile
import unittest

from experiments.androidworld.config import load_config, load_worker_config


class ConfigTest(unittest.TestCase):

  def test_loads_inherited_config_and_worker_payload(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / 'base.toml').write_text(
          '[experiment]\nname="base"\nworkers=3\n'
          '[suite]\nseed=7\n', encoding='utf8',
      )
      child = root / 'child.toml'
      child.write_text(
          'extends="base.toml"\n[experiment]\nname="child"\n'
          '[agent]\nname="pi-gui"\nlearning=true\n', encoding='utf8',
      )

      config = load_config(child)

      self.assertEqual(config.experiment.name, 'child')
      self.assertEqual(config.experiment.workers, 3)
      self.assertEqual(config.suite.seed, 7)
      self.assertTrue(config.agent.learning)
      self.assertFalse(config.agent.enable_ledger_tool)
      self.assertFalse(config.agent.disable_ledger_tool)
      self.assertEqual(config.agent.max_steps, 100)
      self.assertEqual(config.worker_payload(['Task'])['agent']['max_steps'], 100)
      self.assertEqual(config.worker_payload(['Task'])['suite']['tasks'], ['Task'])

  def test_rejects_unknown_keys(self):
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'bad.toml'
      path.write_text(
          '[experiment]\nname="bad"\nworkerz=2\n', encoding='utf8',
      )
      with self.assertRaisesRegex(ValueError, 'workerz'):
        load_config(path)

  def test_loads_generated_worker_json(self):
    config = load_config('configs/androidworld/main.toml')
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'worker.json'
      import json
      path.write_text(json.dumps(config.worker_payload(['SystemWifiTurnOn'])), encoding='utf8')
      worker = load_worker_config(path)
    self.assertEqual(worker.agent.name, 'pi-gui')
    self.assertFalse(worker.agent.enable_ledger_tool)
    self.assertFalse(worker.agent.disable_ledger_tool)
    self.assertEqual(worker.suite.tasks, ('SystemWifiTurnOn',))

  def test_ledger_mcp_flag_is_opt_in_and_reaches_worker(self):
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'ledger.toml'
      path.write_text(
          '[experiment]\nname="ledger"\n'
          '[agent]\nname="codex"\nenable_ledger_tool=true\n',
          encoding='utf8',
      )
      config = load_config(path)
    self.assertTrue(config.agent.enable_ledger_tool)
    self.assertTrue(
        config.worker_payload(['Task'])['agent']['enable_ledger_tool'],
    )

  def test_ledger_mcp_flag_must_be_boolean(self):
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'bad-ledger.toml'
      path.write_text(
          '[experiment]\nname="bad-ledger"\n'
          '[agent]\nenable_ledger_tool="true"\n', encoding='utf8',
      )
      with self.assertRaisesRegex(ValueError, 'must be a boolean'):
        load_config(path)

  def test_pi_ledger_disable_flag_is_opt_in(self):
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'disable-ledger.toml'
      path.write_text(
          '[experiment]\nname="disable-ledger"\n'
          '[agent]\nname="pi-gui"\ndisable_ledger_tool=true\n',
          encoding='utf8',
      )
      config = load_config(path)
    self.assertTrue(config.agent.disable_ledger_tool)
    self.assertTrue(
        config.worker_payload(['Task'])['agent']['disable_ledger_tool'],
    )

  def test_main_benchmark_uses_fresh_avds_per_worker(self):
    config = load_config('configs/androidworld/main.toml')
    self.assertNotIn('avd_cache_dir', config.container.__dataclass_fields__)
    self.assertIn(config.suite.setup_mode, ('auto', 'always'))

  def test_fix_smoke_covers_agent_and_setup_regressions(self):
    config = load_config('configs/androidworld/smoke-fix.toml')
    self.assertEqual(config.experiment.workers, 3)
    self.assertTrue(config.suite.tasks)
    self.assertTrue(config.agent.learning)
    self.assertEqual(config.suite.setup_mode, 'auto')

  def test_fastapi_transport_is_opt_in(self):
    direct = load_config('configs/androidworld/main.toml')
    fastapi = load_config('configs/androidworld/fastapi-smoke.toml')
    self.assertEqual(direct.suite.transport, 'direct')
    self.assertEqual(fastapi.suite.transport, 'fastapi')
    payload = fastapi.worker_payload(['CameraTakePhoto'])
    self.assertEqual(payload['runtime']['server_url'], 'http://127.0.0.1:5000')

  def test_long_benchmark_throttles_worker_startup(self):
    config = load_config('configs/androidworld/long.toml')
    self.assertEqual(config.experiment.worker_start_interval_seconds, 30)
    self.assertEqual(config.experiment.startup_cpu_max_percent, 30)
    self.assertEqual(config.experiment.startup_cpu_stable_samples, 3)


if __name__ == '__main__':
  unittest.main()
