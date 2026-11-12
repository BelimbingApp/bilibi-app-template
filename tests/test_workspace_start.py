"""Check the application root supplied at the Platform launcher handoff."""
import os
from pathlib import Path
import sys
import shutil
import json
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import workspace
from test_workspace_update import WorkspaceFixture
import test_native_builder


class StartTest(unittest.TestCase):
    def handoff(self, environment):
        platform = mock.MagicMock()
        (platform / '.local/composition' / 'manifest.txt').exists.return_value = False
        handed_off = {}

        def execvp(command, arguments):
            handed_off.update(command=command, arguments=arguments, environment=dict(os.environ))

        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(workspace, 'PLATFORM', platform), \
                mock.patch.object(workspace, 'setup'), \
                mock.patch.object(workspace, 'status'), \
                mock.patch.object(os, 'chdir'), \
                mock.patch.object(os, 'execvp', side_effect=execvp):
            workspace.start()
        self.assertEqual(handed_off['command'], 'bash')
        return handed_off['environment']

    def test_supplies_application_root_to_launcher(self):
        env = self.handoff({'BILIBI_SOURCE_DIR': '/different/sources'})
        self.assertEqual(env['BILIBI_APPLICATION_ROOT'], str(workspace.ROOT))

    def test_preserves_explicit_application_root(self):
        env = self.handoff({'BILIBI_APPLICATION_ROOT': '/chosen/application'})
        self.assertEqual(env['BILIBI_APPLICATION_ROOT'], '/chosen/application')

    def test_arch_start_and_platform_setup_inherit_pinned_tools(self):
        platform = mock.MagicMock()
        (platform / '.local/composition' / 'manifest.txt').exists.return_value = False
        environments = []

        def prepare(config, development=False):
            self.assertTrue(development)
            os.environ.update(PATH='/checked/native-tools/bin:' + os.environ['PATH'],
                              RUSTUP_TOOLCHAIN='1.99.0')

        with mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True), \
                mock.patch.object(workspace, 'PLATFORM', platform), \
                mock.patch.object(workspace.recipe, 'materialize'), \
                mock.patch.object(workspace, 'hooks'), \
                mock.patch.object(workspace, 'output_paths'), \
                mock.patch.object(workspace.arch_prerequisites, 'is_arch', return_value=True), \
                mock.patch.object(workspace.native_builder, 'read', return_value={}), \
                mock.patch.object(workspace.native_builder, 'prepare', side_effect=prepare), \
                mock.patch.object(workspace, 'run', side_effect=lambda *args: environments.append(dict(os.environ))), \
                mock.patch.object(workspace, 'status'), \
                mock.patch.object(os, 'chdir'), \
                mock.patch.object(os, 'execvp', side_effect=lambda *args: environments.append(dict(os.environ))):
            workspace.start()
        self.assertEqual(len(environments), 3)  # Platform setup, builder evidence, launcher.
        for env in environments:
            self.assertTrue(env['PATH'].startswith('/checked/native-tools/bin:'))
            self.assertEqual(env['RUSTUP_TOOLCHAIN'], '1.99.0')

    def test_non_arch_prepares_only_node_before_platform_prerequisites(self):
        events = []
        def select_node(*args, **kwargs):
            self.assertTrue(kwargs['development'])
            events.append('node')
            os.environ['PATH'] = '/private/node/bin:' + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True), \
                mock.patch.object(workspace.recipe, 'materialize'), \
                mock.patch.object(workspace, 'hooks'), \
                mock.patch.object(workspace, 'output_paths'), \
                mock.patch.object(workspace.arch_prerequisites, 'is_arch', return_value=False), \
                mock.patch.object(workspace.native_builder, 'read', return_value={}), \
                mock.patch.object(workspace.native_builder, 'select_node', side_effect=select_node), \
                mock.patch.object(workspace.native_builder, 'prepare', side_effect=AssertionError('too early')), \
                mock.patch.object(workspace, 'run', side_effect=lambda *args: events.append((args, os.environ['PATH']))):
            workspace.setup()
        self.assertEqual(events[0], 'node')
        self.assertEqual(events[1][0][1], workspace.TOOLS / 'scripts/dev-setup.sh')
        self.assertEqual(events[2][0][2:], ('--development', '--setup'))
        self.assertTrue(all(event[1].startswith('/private/node/bin:') for event in events[1:]))


class ExecutableStartTest(WorkspaceFixture):
    def test_start_carries_installed_or_fallback_node_through_setup_and_launcher(self):
        harness, native_env = test_native_builder.NativeBuilderTest().fixture(self.app)
        self.env.update(native_env)
        home = self.root / 'isolated-home'
        home.mkdir()
        self.env['HOME'] = str(home)
        for name in ('git', 'bash', 'python3', 'dirname'):
            path = self.app / 'bin' / name
            path.unlink(missing_ok=True)
            path.symlink_to(shutil.which(name))
        # Keep native preparation and workspace handoff real. Replace only the
        # Platform/database launcher and download transport inside the fixture.
        native = self.app / 'scripts/native_builder.py'
        native.write_text(native.read_text().replace('def main():',
            "urllib.request.urlretrieve = lambda url, dest: shutil.copyfile(os.environ['FIXTURE_ARCHIVE'], dest)\n\ndef main():"))
        (self.app / 'scripts/dev-setup.sh').write_text('node --version; npm --version\n')
        launcher = self.platform / 'scripts/start-app.sh'
        launcher.write_text('set -e\nnode --version\nnpm --version\necho FIXTURE_LAUNCHER_READY\n')
        output = self.command('start')
        self.assertIn('Node v26.13.0 (installed), npm 11.20.0', output)
        self.assertIn('v26.13.0\n11.20.0\nFIXTURE_LAUNCHER_READY', output)
        self.assertFalse((self.app / 'build/native-tools/node-24.6.0').exists())
        self.env['AMBIENT_NODE_VERSION'] = 'v20.19.0'
        output = self.command('start')
        self.assertIn('Node v24.6.0 (pinned), npm 11.5.1', output)
        self.assertIn('v24.6.0\n11.5.1\nFIXTURE_LAUNCHER_READY', output)
        saved = json.loads((self.app / 'build/evidence/native-builder.json').read_text())
        self.assertEqual(saved['node_selection'], 'pinned')


if __name__ == '__main__':
    unittest.main()
