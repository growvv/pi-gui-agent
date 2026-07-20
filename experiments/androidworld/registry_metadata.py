"""Read structured task metadata from an AndroidWorld registry image."""

from __future__ import annotations

import json
import subprocess
from typing import Any


_REGISTRY_SCRIPT = r'''
import json
import sys

from android_world import registry

tasks = registry.TaskRegistry().get_registry(sys.argv[1])
metadata = {}
for name, task_class in tasks.items():
  app_names = getattr(task_class, "app_names", ()) or ()
  if not app_names:
    task = task_class(task_class.generate_random_params())
    app_names = getattr(task, "app_names", ()) or ()
  if isinstance(app_names, str):
    app_names = (app_names,)
  metadata[name] = {
      "complexity": float(getattr(task_class, "complexity", 1)),
      "app_names": list(app_names),
  }
print(json.dumps(metadata))
'''


def load_task_registry_metadata(
    image: str, family: str = 'android_world',
) -> dict[str, dict[str, Any]]:
  """Load task complexity and app names from an AndroidWorld Docker image."""
  result = subprocess.run(
      [
          'docker', 'run', '--rm', '--entrypoint', 'python3', image,
          '-c', _REGISTRY_SCRIPT, family,
      ],
      check=True, capture_output=True, text=True,
  )
  value = json.loads(result.stdout)
  if not isinstance(value, dict):
    raise ValueError('AndroidWorld registry metadata must be a JSON object')
  metadata = {}
  for name, row in value.items():
    if not isinstance(name, str) or not isinstance(row, dict):
      raise ValueError('AndroidWorld registry metadata contains an invalid task')
    app_names = row.get('app_names', [])
    if not isinstance(app_names, list) or not all(
        isinstance(app_name, str) for app_name in app_names
    ):
      raise ValueError(f'Invalid app_names for AndroidWorld task {name}')
    metadata[name] = {
        'complexity': float(row.get('complexity', 1)),
        'app_names': app_names,
    }
  return metadata
