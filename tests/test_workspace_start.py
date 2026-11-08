"""Check the application root supplied at the Platform launcher handoff."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import workspace


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

        def prepare(config):
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


if __name__ == '__main__':
    unittest.main()
