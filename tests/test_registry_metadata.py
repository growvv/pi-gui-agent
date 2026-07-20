import json
import unittest
from unittest import mock

from experiments.androidworld.registry_metadata import load_task_registry_metadata


class RegistryMetadataTest(unittest.TestCase):

  @mock.patch('experiments.androidworld.registry_metadata.subprocess.run')
  def test_loads_complexity_and_app_names_from_registry_image(self, run):
    run.return_value = mock.Mock(stdout=json.dumps({
        'MarkorCreateNoteFromClipboard': {
            'complexity': 1.4,
            'app_names': ['markor', 'clipper'],
        },
    }))

    metadata = load_task_registry_metadata('androidworld:test')

    self.assertEqual(
        metadata['MarkorCreateNoteFromClipboard']['app_names'],
        ['markor', 'clipper'],
    )
    command = run.call_args.args[0]
    self.assertEqual(command[:5], [
        'docker', 'run', '--rm', '--entrypoint', 'python3',
    ])
    self.assertEqual(command[5], 'androidworld:test')
    self.assertEqual(command[-1], 'android_world')

  @mock.patch('experiments.androidworld.registry_metadata.subprocess.run')
  def test_rejects_invalid_app_names(self, run):
    run.return_value = mock.Mock(stdout=json.dumps({
        'Task': {'complexity': 1, 'app_names': 'settings'},
    }))
    with self.assertRaisesRegex(ValueError, 'app_names'):
      load_task_registry_metadata('androidworld:test')


if __name__ == '__main__':
  unittest.main()
