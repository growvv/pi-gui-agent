from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


class _Result:
  def __init__(self, done, data):
    self.done = done
    self.data = data


class _BaseAgent:
  def __init__(self, env, name='', transition_pause=None):
    self.env = env
    self.name = name
    self._max_steps = None

  def reset(self, go_home=False):
    self.env.reset(go_home=go_home)

  def set_max_steps(self, value):
    self._max_steps = value


android_world = types.ModuleType('android_world')
agents_package = types.ModuleType('android_world.agents')
base_agent = types.ModuleType('android_world.agents.base_agent')
base_agent.EnvironmentInteractingAgent = _BaseAgent
base_agent.AgentInteractionResult = _Result
env_package = types.ModuleType('android_world.env')
interface = types.ModuleType('android_world.env.interface')
interface.AsyncEnv = object
agents_package.base_agent = base_agent
env_package.interface = interface
sys.modules.setdefault('android_world', android_world)
sys.modules.setdefault('android_world.agents', agents_package)
sys.modules.setdefault('android_world.agents.base_agent', base_agent)
sys.modules.setdefault('android_world.env', env_package)
sys.modules.setdefault('android_world.env.interface', interface)

from baselines.claude_code import ClaudeCodeAgent, claude_session_id
from baselines.codex import CodexAgent
from baselines.openclaw import OpenClawAgent
from experiments.androidworld.factory import create_agent
from experiments.androidworld.pi_gui_agent import PiGuiAgent


class _Env:
  def __init__(self):
    self.resets = []

  def reset(self, go_home=False):
    self.resets.append(go_home)


class AgentAdapterTest(unittest.TestCase):

  def test_factory_keeps_pi_and_baselines_distinct(self):
    self.assertIsInstance(create_agent('pi-gui', _Env(), workspace_dir='.'), PiGuiAgent)
    self.assertIsInstance(create_agent('claude-code', _Env(), workspace_dir='.'), ClaudeCodeAgent)
    self.assertIsInstance(create_agent('codex', _Env(), workspace_dir='.'), CodexAgent)
    self.assertIsInstance(create_agent('openclaw', _Env(), workspace_dir='.'), OpenClawAgent)

  def test_each_baseline_owns_its_command(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      result = root / 'result.json'
      openclaw_home = root / 'home'
      openclaw_config = openclaw_home / '.openclaw' / 'openclaw.json'
      openclaw_config.parent.mkdir(parents=True)
      openclaw_config.write_text(json.dumps({
          'tools': {'profile': 'coding'},
          'mcp': {'servers': {'ambient': {'url': 'https://example.invalid'}}},
      }), encoding='utf8')
      with mock.patch.dict('os.environ', {
          'ANDROIDWORLD_AGENT_HOME': str(openclaw_home),
      }, clear=True):
        claude = ClaudeCodeAgent(_Env(), workspace_dir='.').build_command('task', 10, result)
        codex = CodexAgent(_Env(), workspace_dir='.').build_command('task', 10, result)
        openclaw = OpenClawAgent(
            _Env(), workspace_dir='.', openclaw_model='mimo',
        ).build_command('task', 10, result)
      config = json.loads(Path(claude[claude.index('--mcp-config') + 1]).read_text())
      generated = json.loads(Path(openclaw[1].partition('=')[2]).read_text())
    self.assertEqual(claude[0], 'claude')
    self.assertEqual(set(config['mcpServers']), {'android-gui'})
    startup_prompt = claude[claude.index('--append-system-prompt') + 1]
    self.assertIn('WaitForMcpServers', startup_prompt)
    self.assertIn('mcp__android-gui__screenshot', startup_prompt)
    self.assertEqual(codex[:2], ['codex', 'exec'])
    self.assertNotIn('mcp_servers.ledger', ' '.join(codex))
    self.assertEqual(openclaw[2:4], ['openclaw', 'agent'])
    self.assertEqual(openclaw[openclaw.index('--agent') + 1], 'main')
    self.assertEqual(set(generated['mcp']['servers']), {'android-gui'})
    self.assertNotIn('ambient', generated['mcp']['servers'])

  def test_ledger_mcp_is_opt_in_for_every_baseline(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      result = root / 'result.json'
      openclaw_home = root / 'home'
      openclaw_config = openclaw_home / '.openclaw' / 'openclaw.json'
      openclaw_config.parent.mkdir(parents=True)
      openclaw_config.write_text('{}', encoding='utf8')
      options = {
          'workspace_dir': '.', 'enable_ledger_tool': True,
          'session_dir': str(root / 'runs'),
      }
      with mock.patch.dict('os.environ', {
          'ANDROIDWORLD_AGENT_HOME': str(openclaw_home),
      }, clear=True):
        claude = ClaudeCodeAgent(_Env(), **options).build_command('task', 10, result)
        codex = CodexAgent(_Env(), **options).build_command('task', 10, result)
        openclaw = OpenClawAgent(_Env(), **options).build_command('task', 10, result)
      claude_config = json.loads(
          Path(claude[claude.index('--mcp-config') + 1]).read_text()
      )
      openclaw_generated = json.loads(
          Path(openclaw[1].partition('=')[2]).read_text()
      )

    self.assertEqual(set(claude_config['mcpServers']), {'android-gui', 'ledger'})
    self.assertIn('mcp_servers.ledger.command="node"', codex)
    self.assertEqual(
        set(openclaw_generated['mcp']['servers']), {'android-gui', 'ledger'},
    )
    ledger_args = claude_config['mcpServers']['ledger']['args']
    self.assertEqual(ledger_args[ledger_args.index('--toolset') + 1], 'ledger')
    self.assertEqual(
        ledger_args[ledger_args.index('--ledger-dir') + 1], str(root / 'ledgers'),
    )

  def test_codex_does_not_embed_secret(self):
    agent = CodexAgent(_Env(), workspace_dir='.')
    with mock.patch.dict('os.environ', {
        'OPENAI_BASE_URL': 'https://gateway.example/v1',
        'OPENAI_API_KEY': 'secret',
    }, clear=True):
      command = agent.build_command('task', 10, Path('/tmp/result'))
    rendered = ' '.join(command)
    self.assertIn('env_key="OPENAI_API_KEY"', rendered)
    self.assertNotIn('secret', rendered)

  def test_pi_runs_one_process_and_reads_explicit_completion(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      entrypoint = root / 'dist' / 'cli.js'
      entrypoint.parent.mkdir()
      entrypoint.write_text(
          "const fs=require('fs');const a=process.argv.slice(2);"
          "fs.writeFileSync(a[a.indexOf('--result-file')+1],"
          "JSON.stringify({finished:true,answer:'done',actions:2,steps:12,ledgerPath:'/output/ledgers/task.sh'}));",
          encoding='utf8',
      )
      env = _Env()
      agent = PiGuiAgent(
          env, workspace_dir=root, pi_gui_dir=root, min_actions=1,
          session_dir='/output/runs',
      )
      result = agent.step('task')
    self.assertTrue(result.done)
    self.assertTrue(result.data['agent_finished'])
    self.assertEqual(result.data['attempts'][0]['actions'], 2)
    self.assertEqual(result.data['attempts'][0]['steps'], 12)
    self.assertEqual(result.data['attempts'][0]['ledger_path'], '/output/ledgers/task.sh')
    self.assertEqual(env.interaction_cache, 'done')
    self.assertEqual(
        result.data['command'][result.data['command'].index('--ledger-dir') + 1],
        '/output/ledgers',
    )
    self.assertIn('--learning-root', result.data['command'])
    self.assertEqual(
        result.data['command'][result.data['command'].index('--max-steps') + 1],
        '100',
    )
    self.assertEqual(result.data['command'][-1], 'task')

  def test_prepares_pi_ledger_directory_for_demoted_agent(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      agent = PiGuiAgent(
          _Env(), workspace_dir=root, pi_gui_dir=root,
          session_dir=str(root / 'runs'),
      )
      account = mock.Mock(pw_uid=123, pw_gid=456)
      with mock.patch.dict('os.environ', {'ANDROIDWORLD_AGENT_USER': 'agent'}), \
           mock.patch('experiments.androidworld.agent.os.geteuid', return_value=0), \
           mock.patch('experiments.androidworld.agent.pwd.getpwnam', return_value=account), \
           mock.patch('experiments.androidworld.agent.os.chown') as chown:
        agent._prepare_agent_directories()

      ledger_dir = root / 'ledgers'
      self.assertTrue(ledger_dir.is_dir())
      self.assertIn(mock.call(ledger_dir, 123, 456), chown.call_args_list)

  def test_pi_ledger_disable_flag_reaches_cli_and_skips_ledger_directory(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      agent = PiGuiAgent(
          _Env(), workspace_dir=root, pi_gui_dir=root,
          session_dir=str(root / 'runs'), disable_ledger_tool=True,
      )
      agent._prepare_agent_directories()
      command = agent.build_command('task', 10, root / 'result.json')
      ledger_exists = (root / 'ledgers').exists()
    self.assertIn('--disable-ledger-tool', command)
    self.assertNotIn('--ledger-dir', command)
    self.assertFalse(ledger_exists)

  def test_extracts_claude_session_id(self):
    self.assertEqual(
        claude_session_id('{"type":"result","session_id":"abc"}\n'), 'abc',
    )

  def test_claude_prompt_requires_mcp_startup_before_fallbacks(self):
    prompt = ClaudeCodeAgent(_Env(), workspace_dir='.')._prompt('task')
    self.assertLess(prompt.index('WaitForMcpServers'), prompt.index('Task: task'))
    self.assertIn('Do not use Bash, ADB, Read', prompt)

  def test_prepares_mcp_screenshot_directory_for_demoted_agent(self):
    with tempfile.TemporaryDirectory() as temporary:
      agent = ClaudeCodeAgent(
          _Env(), workspace_dir='.', session_dir=str(Path(temporary) / 'runs'),
      )
      account = mock.Mock(pw_uid=123, pw_gid=456)
      with mock.patch.dict('os.environ', {'ANDROIDWORLD_AGENT_USER': 'agent'}), \
           mock.patch('experiments.androidworld.agent.os.geteuid', return_value=0), \
           mock.patch('experiments.androidworld.agent.pwd.getpwnam', return_value=account), \
           mock.patch('experiments.androidworld.agent.os.chown') as chown:
        agent._prepare_agent_directories()
      screenshot_dir = Path(temporary) / 'runs' / 'mcp-screenshots'
      self.assertTrue(screenshot_dir.is_dir())
      self.assertEqual(chown.call_args_list, [
          mock.call(screenshot_dir.parent, 123, 456),
          mock.call(screenshot_dir, 123, 456),
      ])

  def test_prepares_opt_in_mcp_ledger_directory_for_demoted_agent(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      agent = ClaudeCodeAgent(
          _Env(), workspace_dir='.', session_dir=str(root / 'runs'),
          enable_ledger_tool=True,
      )
      account = mock.Mock(pw_uid=123, pw_gid=456)
      with mock.patch.dict('os.environ', {'ANDROIDWORLD_AGENT_USER': 'agent'}), \
           mock.patch('experiments.androidworld.agent.os.geteuid', return_value=0), \
           mock.patch('experiments.androidworld.agent.pwd.getpwnam', return_value=account), \
           mock.patch('experiments.androidworld.agent.os.chown') as chown:
        agent._prepare_agent_directories()
      self.assertTrue((root / 'ledgers').is_dir())
      self.assertIn(mock.call(root / 'ledgers', 123, 456), chown.call_args_list)


if __name__ == '__main__':
  unittest.main()
