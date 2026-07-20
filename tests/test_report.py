import gzip
import json
from pathlib import Path
import pickle
import tempfile
import unittest

from experiments.androidworld import report


class ReportTest(unittest.TestCase):

  def test_reads_checkpoints_without_androidworld_import(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      checkpoints = root / 'worker-1' / 'checkpoints'
      checkpoints.mkdir(parents=True)
      with gzip.open(checkpoints / 'task.pkl.gz', 'wb') as stream:
        pickle.dump([{
            'task_template': 'Task', 'is_successful': 1.0,
            'exception_info': None, 'run_time': 3.0,
        }], stream)
      rows = report.checkpoint_rows(root)
    self.assertEqual(rows['Task']['success'], 1.0)

  def test_drops_inline_images_from_sessions(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      runs = root / 'worker-1' / 'runs'
      runs.mkdir(parents=True)
      event = {
          'type': 'message',
          'message': {'role': 'user', 'content': [
              {'type': 'text', 'text': 'Task:\n\nOpen Settings\n\nThe attached image'},
              {'type': 'image', 'data': 'large'},
          ]},
      }
      (runs / 'session.jsonl').write_text(json.dumps(event), encoding='utf8')
      sessions = report.load_sessions(root)
    self.assertEqual(sessions[0]['goal'], 'Open Settings')
    content = sessions[0]['events'][0]['message']['content']
    self.assertFalse(any(item.get('type') == 'image' for item in content))
    self.assertEqual(sessions[0]['events'][0]['_report_root'], str(root))

  def test_normalizes_pi_gui_task_prefix(self):
    event = {'message': {'role': 'user', 'content': [{
        'type': 'text',
        'text': 'Context\n\nTask: Open Settings\n\nThe attached image',
    }]}}
    self.assertEqual(report.task_goal([event], Path('/missing')), 'Open Settings')

  def test_reads_task_at_start_of_pi_gui_prompt(self):
    event = {'message': {'role': 'user', 'content': [{
        'type': 'text',
        'text': 'Task: Open Settings\n\nThe attached image is the current phone screen.',
    }]}}
    self.assertEqual(report.task_goal([event], Path('/missing')), 'Open Settings')

  def test_reads_last_task_from_legacy_pi_gui_prompt(self):
    event = {'message': {'role': 'user', 'content': [{
        'type': 'text',
        'text': ('Task: Follow the AndroidWorld instructions.\n\n'
                 'Task: Open Settings\n\nThe attached image is the current phone screen.'),
    }]}}
    self.assertEqual(report.task_goal([event], Path('/missing')), 'Open Settings')

  def test_parses_claude_tool_result_without_inline_image(self):
    event = {
        'type': 'user',
        'message': {'role': 'user', 'content': [{
            'type': 'tool_result', 'content': [
                {'type': 'text', 'text': 'Screenshot archive: /output/shot.png'},
                {'type': 'image', 'source': {'data': 'large'}},
            ],
        }]},
    }
    compacted = report.compact_event(event)
    label, body, kind = report.event_info(compacted)
    nested = compacted['message']['content'][0]['content']
    self.assertEqual((label, kind), ('Action result', 'action'))
    self.assertIn('Screenshot archive:', body)
    self.assertFalse(any(item.get('type') == 'image' for item in nested))

  def test_reads_claude_token_names(self):
    usage = report.token_usage([{'usage': {
        'input_tokens': 10, 'output_tokens': 3,
        'cache_read_input_tokens': 20,
        'cache_creation_input_tokens': 4,
    }}])
    self.assertEqual(usage['total'], 37)
    self.assertEqual(usage['cacheRead'], 20)

  def test_finds_mcp_screenshot(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      screenshot = root / 'worker-1' / 'runs' / 'mcp-screenshots' / 'shot.png'
      screenshot.parent.mkdir(parents=True)
      screenshot.touch()
      event = {'message': {'details': {'archivePath': '/output/shot.png'}}}
      found = report.screenshot_ref(event, '', root)
    self.assertEqual(found, screenshot)

  def test_merges_result_directories_in_order(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      first = root / 'first'
      retry = root / 'retry'
      output = first / 'report'
      self._checkpoint(first, 'TaskA', 0.0)
      self._checkpoint(retry, 'TaskB', 1.0)
      (first / 'manifest.json').write_text('{}', encoding='utf8')
      (retry / 'manifest.json').write_text('{}', encoding='utf8')
      count = report.generate_report([first, retry], output)
      index = (output / 'index.html').read_text(encoding='utf8')
    self.assertEqual(count, 2)
    self.assertIn('TaskA', index)
    self.assertIn('TaskB', index)

  @staticmethod
  def _checkpoint(root, task, success):
    checkpoints = root / 'worker-1' / 'checkpoints'
    checkpoints.mkdir(parents=True)
    with gzip.open(checkpoints / f'{task}.pkl.gz', 'wb') as stream:
      pickle.dump([{
          'task_template': task, 'is_successful': success,
          'exception_info': None, 'run_time': 1.0,
      }], stream)


if __name__ == '__main__':
  unittest.main()
