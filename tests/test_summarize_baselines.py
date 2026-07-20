from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
import pickle
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'summarize-baselines.py'
SPEC = importlib.util.spec_from_file_location('summarize_baselines', SCRIPT)
assert SPEC and SPEC.loader
summarize_baselines = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summarize_baselines)


class BaselineSummaryTest(unittest.TestCase):

  def test_merges_failed_run_with_retry_and_prefers_retry_result(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      self._run(root, 'base', 'failed', '2026-01-01', [
          {'task_template': 'A', 'is_successful': 0.0, 'exception_info': None},
          {'task_template': 'B', 'is_successful': 0.0, 'exception_info': 'bad'},
      ], expected=3)
      self._run(root, 'retry', 'completed', '2026-01-02', [
          {'task_template': 'B', 'is_successful': 1.0, 'exception_info': None},
          {'task_template': 'C', 'is_successful': 1.0, 'exception_info': None},
      ], expected=2)
      self._run(root, 'running', 'running', '2026-01-03', [
          {'task_template': 'D', 'is_successful': 1.0, 'exception_info': None},
      ], expected=4)

      rows = summarize_baselines.baseline_rows(root)

    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]['expected'], 3)
    self.assertEqual(rows[0]['recorded'], 3)
    self.assertEqual(rows[0]['completed'], 3)
    self.assertEqual(rows[0]['exceptions'], 0)
    self.assertEqual(rows[0]['successful'], 2.0)
    self.assertAlmostEqual(rows[0]['success_rate'], 2 / 3)
    self.assertEqual(len(rows[0]['sources']), 2)

  @staticmethod
  def _run(root, suffix, status, started_at, episodes, expected):
    run = root / f'androidworld-baseline-claude-code-{suffix}'
    checkpoints = run / 'worker-1' / 'checkpoints'
    checkpoints.mkdir(parents=True)
    (run / 'manifest.json').write_text(json.dumps({
        'agent': 'claude-code', 'status': status,
        'started_at': started_at, 'expected_episodes': expected,
    }), encoding='utf8')
    for index, episode in enumerate(episodes):
      with gzip.open(checkpoints / f'{index}.pkl.gz', 'wb') as stream:
        pickle.dump([episode], stream)


if __name__ == '__main__':
  unittest.main()
