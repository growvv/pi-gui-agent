from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path


class _Result:
  def __init__(self, done, data):
    self.done = done
    self.data = data


class _BaseAgent:
  def __init__(self, env, name='', transition_pause=1.0):
    self.env = env
    self.name = name
    self.transition_pause = transition_pause
    self._max_steps = None

  def reset(self, go_home=False):
    self.env.reset(go_home=go_home)

  def set_max_steps(self, value):
    self._max_steps = value


android_world = types.ModuleType('android_world')
agents = types.ModuleType('android_world.agents')
base_agent = types.ModuleType('android_world.agents.base_agent')
base_agent.EnvironmentInteractingAgent = _BaseAgent
base_agent.AgentInteractionResult = _Result
env_package = types.ModuleType('android_world.env')
interface = types.ModuleType('android_world.env.interface')
interface.AsyncEnv = object
agents.base_agent = base_agent
env_package.interface = interface
sys.modules.setdefault('android_world', android_world)
sys.modules.setdefault('android_world.agents', agents)
sys.modules.setdefault('android_world.agents.base_agent', base_agent)
sys.modules.setdefault('android_world.env', env_package)
sys.modules.setdefault('android_world.env.interface', interface)

from benchmark.android_world_agent import PiGuiAgent


class _Env:
  def __init__(self):
    self.resets = []

  def reset(self, go_home=False):
    self.resets.append(go_home)


class PiGuiAgentTest(unittest.TestCase):
  def test_maps_episode_budget_and_captures_node_result(self):
    with tempfile.TemporaryDirectory() as directory:
      entrypoint = Path(directory, 'dist', 'cli.js')
      entrypoint.parent.mkdir()
      entrypoint.write_text(
          "const fs=require('fs'); const a=process.argv.slice(2); "
          "fs.writeFileSync(a[a.indexOf('--result-file')+1], "
          "JSON.stringify({finished:true,answer:'wifi is on'})); "
          "console.log(JSON.stringify(a));",
          encoding='utf8',
      )
      env = _Env()
      agent = PiGuiAgent(
          env,
          project_dir=directory,
          adb_path='/sdk/adb',
          serial='emulator-5556',
          learning=False,
      )
      agent.reset(go_home=True)
      agent.set_max_steps(17)
      result = agent.step('turn wifi on')

      self.assertTrue(result.done)
      self.assertTrue(result.data['agent_finished'])
      self.assertEqual(result.data['max_actions'], 17)
      self.assertIn('--no-learning', result.data['command'])
      self.assertIn('emulator-5556', result.data['command'])
      self.assertIn('turn wifi on', result.data['stdout'])
      self.assertEqual(env.resets, [True])
      self.assertEqual(env.interaction_cache, 'wifi is on')

  def test_refuses_to_run_twice_in_one_episode(self):
    with tempfile.TemporaryDirectory() as directory:
      entrypoint = Path(directory, 'dist', 'cli.js')
      entrypoint.parent.mkdir()
      entrypoint.write_text('', encoding='utf8')
      agent = PiGuiAgent(_Env(), project_dir=directory)
      first = agent.step('task')
      second = agent.step('task')
      self.assertFalse(first.done)
      self.assertFalse(second.done)
      self.assertIn('already run', second.data['error'])


if __name__ == '__main__':
  unittest.main()
