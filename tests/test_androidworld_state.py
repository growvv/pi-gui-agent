from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiments.androidworld.state import (
    StateTrackingCheckpointer,
    write_worker_state,
)


class AndroidWorldStateTest(unittest.TestCase):

  def test_tracking_checkpointer_reports_saved_episodes(self):
    saved = []

    class Checkpointer:
      def save_episodes(self, episodes, checkpoint_name):
        saved.append((episodes, checkpoint_name))
        return 'saved'

    updates = []
    tracker = StateTrackingCheckpointer(
        Checkpointer(), lambda episodes: updates.append(list(episodes)),
    )
    episode = {'task_template': 'Task'}

    self.assertEqual(tracker.save_episodes([episode], 'Task_0'), 'saved')
    self.assertEqual(saved, [([episode], 'Task_0')])
    self.assertEqual(updates, [[episode]])

  def test_records_task_results_and_step_limit(self):
    episodes = [{
        'task_template': 'LimitedTask',
        'instance_id': 0,
        'goal': 'Complete the limited task',
        'is_successful': 0.0,
        'exception_info': None,
        'episode_data': {'agent_result': [{
            'agent_finished': False,
            'returncode': 2,
            'attempts': [{
                'attempt': 1,
                'actions': 20,
                'steps': 100,
                'aborted': True,
                'abort_reason': (
                    'Step limit reached: maximum 100 Thinking & action steps.'
                ),
                'finished': False,
            }],
        }]},
    }]
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'state.json'
      write_worker_state(
          path, ('LimitedTask', 'PendingTask'), 1, episodes, 100, 'running',
      )
      state = json.loads(path.read_text(encoding='utf8'))

    self.assertEqual(state['status'], 'running')
    self.assertEqual(state['max_steps'], 100)
    limited, pending = state['tasks']
    self.assertEqual(limited['status'], 'failed')
    self.assertTrue(limited['reached_max_steps'])
    self.assertEqual(limited['result']['attempts'][0]['steps'], 100)
    self.assertEqual(pending['status'], 'pending')
    self.assertFalse(pending['reached_max_steps'])

  def test_records_success_and_exception(self):
    episodes = [
        {
            'task_template': 'SuccessfulTask',
            'instance_id': 0,
            'goal': 'Succeed',
            'is_successful': 1.0,
            'exception_info': None,
            'episode_data': {'agent_result': [{'agent_finished': True}]},
        },
        {
            'task_template': 'ExceptionTask',
            'instance_id': 0,
            'goal': 'Raise',
            'is_successful': 0.0,
            'exception_info': 'traceback',
            'episode_data': {'agent_result': [{}]},
        },
    ]
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / 'state.json'
      write_worker_state(
          path, ('SuccessfulTask', 'ExceptionTask'), 1, episodes, 100,
          'completed',
      )
      state = json.loads(path.read_text(encoding='utf8'))

    self.assertEqual(state['tasks'][0]['status'], 'success')
    self.assertEqual(state['tasks'][1]['status'], 'exception')


if __name__ == '__main__':
  unittest.main()
