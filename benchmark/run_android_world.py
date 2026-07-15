#!/usr/bin/env python3
"""Run pi-gui-agent against an existing AndroidWorld installation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from android_world import checkpointer as checkpointer_lib
from android_world import registry
from android_world import suite_utils
from android_world.env import env_launcher

from android_world_agent import PiGuiAgent


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  parser.add_argument('--project-dir', default=str(Path(__file__).resolve().parents[1]))
  parser.add_argument('--adb-path', default='adb')
  parser.add_argument('--node-path', default='node')
  parser.add_argument('--console-port', type=int, default=5554)
  parser.add_argument('--grpc-port', type=int, default=8554)
  parser.add_argument('--perform-emulator-setup', action='store_true')
  parser.add_argument('--suite-family', default=registry.TaskRegistry.ANDROID_WORLD_FAMILY)
  parser.add_argument('--tasks', nargs='*')
  parser.add_argument('--task-random-seed', type=int, default=30)
  parser.add_argument('--n-task-combinations', type=int, default=1)
  parser.add_argument('--fixed-task-seed', action='store_true')
  parser.add_argument('--checkpoint-dir', default='benchmark-results')
  parser.add_argument('--timeout-seconds', type=int, default=900)
  parser.add_argument('--learning', action='store_true')
  return parser.parse_args()


def main() -> None:
  args = parse_args()
  os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')
  os.environ.setdefault('GRPC_TRACE', 'none')

  env = env_launcher.load_and_setup_env(
      console_port=args.console_port,
      emulator_setup=args.perform_emulator_setup,
      adb_path=args.adb_path,
      grpc_port=args.grpc_port,
  )
  try:
    task_registry = registry.TaskRegistry()
    suite = suite_utils.create_suite(
        task_registry.get_registry(family=args.suite_family),
        n_task_combinations=args.n_task_combinations,
        seed=args.task_random_seed,
        tasks=args.tasks,
        use_identical_params=args.fixed_task_seed,
        env=env,
    )
    suite.suite_family = args.suite_family
    agent = PiGuiAgent(
        env,
        project_dir=args.project_dir,
        adb_path=args.adb_path,
        serial=f'emulator-{args.console_port}',
        node_path=args.node_path,
        timeout_seconds=args.timeout_seconds,
        learning=args.learning,
    )
    results = suite_utils.run(
        suite,
        agent,
        checkpointer=checkpointer_lib.IncrementalCheckpointer(args.checkpoint_dir),
        demo_mode=False,
    )
    suite_utils.process_episodes(results, print_summary=True)
  finally:
    env.close()


if __name__ == '__main__':
  main()
