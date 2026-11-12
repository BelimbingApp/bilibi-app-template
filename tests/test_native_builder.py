"""Exercise the native tool gate and inspect actual ELF executables."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts/native_builder.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('native_builder', SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NativeBuilderTest(unittest.TestCase):
    def fixture(self, root):
        tools = root / 'bin'
        tools.mkdir()
        config = builder.read(SCRIPT.parent.parent / 'builder.toml')
        commands = {'rustup': 'if [ \"$1 $2\" = \"toolchain list\" ]; then echo 1.99.0-x86_64-unknown-linux-gnu; fi',
                    'rustc': 'echo "rustc 1.99.0"', 'cargo': 'echo "cargo 1.99.0"',
                    'zig': 'echo 0.14.1', 'cargo-zigbuild': 'echo "cargo-zigbuild 0.24.1"',
                    'node': 'echo "${AMBIENT_NODE_VERSION:-v26.13.0}"',
                    'npm': 'echo 11.20.0; exit "${AMBIENT_NPM_EXIT:-0}"'}
        for name in ('git', 'curl', 'objdump', 'cc', 'psql', 'pacman'):
            commands[name] = 'exit 0'
        for name, body in commands.items():
            path = tools / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        archive = root / 'node.tar.xz'
        with tarfile.open(archive, 'w:xz') as stream:
            for name, version in [('node', 'v24.6.0'), ('npm', '11.5.1')]:
                body = ('#!/bin/sh\necho ' + version + '\n').encode()
                info = tarfile.TarInfo('node-v24.6.0-linux-x64/bin/' + name)
                info.size, info.mode = len(body), 0o755
                stream.addfile(info, io.BytesIO(body))
        config['node_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        (root / 'builder.toml').write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in config.items()))
        shutil.copyfile(SCRIPT.parent.parent / 'rust-toolchain.toml', root / 'rust-toolchain.toml')
        # Replace only the download transport; execute real preparation and CLI
        # handoff, with small external-tool executables instead of installations.
        harness = root / 'native.py'
        harness.write_text(f"""import sys, shutil, os
sys.path.insert(0, {str(SCRIPT.parent)!r})
import native_builder
native_builder.urllib.request.urlretrieve = lambda url, dest: shutil.copyfile(os.environ['FIXTURE_ARCHIVE'], dest)
native_builder.main()
""")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('GIT_') and key not in ('CI', 'GITHUB_ACTIONS', 'CARGO_TARGET_DIR')}
        env.update(PATH=str(tools), BILIBI_TOOL_APPLICATION_ROOT=str(root),
                   BILIBI_BUILD_DIR=str(root / 'build'), FIXTURE_ARCHIVE=str(archive))
        return harness, env

    def invoke(self, harness, env, *args):
        return subprocess.run([sys.executable, str(harness), *args], env=env,
                              capture_output=True, text=True, timeout=15)

    def test_development_executes_installed_node_and_records_actual_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness, env = self.fixture(root)
            env['FIXTURE_ARCHIVE'] = '/no-download-allowed'
            result = self.invoke(harness, env, '--development', 'node', '--version')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'v26.13.0')
            saved = json.loads((root / 'build/evidence/native-builder.json').read_text())
            self.assertEqual((saved['node'], saved['npm'], saved['node_selection']),
                             ('v26.13.0', '11.20.0', 'installed'))
            self.assertFalse((root / 'build/native-tools/node-24.6.0').exists())

    def test_missing_old_prerelease_or_broken_npm_automatically_uses_private_node(self):
        for mode in ('missing', 'old', 'prerelease', 'npm'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                harness, env = self.fixture(root)
                if mode == 'missing':
                    (root / 'bin/node').unlink()
                elif mode == 'old':
                    env['AMBIENT_NODE_VERSION'] = 'v20.19.0'
                elif mode == 'prerelease':
                    env['AMBIENT_NODE_VERSION'] = 'v27.0.0-alpha.1'
                else:
                    env['AMBIENT_NPM_EXIT'] = '1'
                result = self.invoke(harness, env, '--development', 'node', '--version')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'v24.6.0')
                saved = json.loads((root / 'build/evidence/native-builder.json').read_text())
                self.assertEqual((saved['node_selection'], saved['npm']), ('pinned', '11.5.1'))
                # The existing private selection works without another download.
                env['FIXTURE_ARCHIVE'] = '/no-download-allowed'
                self.assertEqual(self.invoke(harness, env, '--development', '--setup').returncode, 0)

    def test_ci_and_internal_artifact_builds_always_use_private_pin(self):
        for ci in ('', 'CI', 'GITHUB_ACTIONS'):
            with self.subTest(ci=ci), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                harness, env = self.fixture(root)
                args = ['--development'] if ci else []
                if ci:
                    env[ci] = 'true'
                result = self.invoke(harness, env, *args, 'node', '--version')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'v24.6.0')

    def test_bad_download_checksum_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness, env = self.fixture(root)
            (root / 'node.tar.xz').write_bytes(b'changed archive')
            result = self.invoke(harness, env, '--setup')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Node download checksum differs', result.stderr)
            self.assertFalse((root / 'build/native-tools/node-24.6.0').exists())

    def test_changed_cached_private_node_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness, env = self.fixture(root)
            self.assertEqual(self.invoke(harness, env, '--setup').returncode, 0)
            (root / 'build/native-tools/node-24.6.0/bin/node').write_text('#!/bin/sh\necho v24.6.0\n# changed\n')
            result = self.invoke(harness, env, '--setup')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('private Node changed', result.stderr)

    def test_packaging_cannot_relabel_local_build_or_changed_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness, env = self.fixture(root)
            target = root / 'build/target/x86_64-unknown-linux-gnu/release'
            target.mkdir(parents=True)
            for name in ('bilibi-web', 'bilibi-installer'):
                (target / name).write_bytes(name.encode())
            config = builder.read(root / 'builder.toml')
            self.assertEqual(self.invoke(harness, env, '--development', '--setup').returncode, 0)
            self.assertEqual(self.invoke(harness, env, '--record-build').returncode, 0)
            # Preparing a pinned environment afterwards must not rewrite which
            # Node compiled the existing bytes.
            self.assertEqual(self.invoke(harness, env, '--setup').returncode, 0)
            with mock.patch.object(builder, 'BUILD', root / 'build'):
                with self.assertRaisesRegex(ValueError, 'requires a build with the pinned Node'):
                    builder.qualified_toolchain(config, target)
                # Simulate new compiled bytes from the pinned build step.
                for name in ('bilibi-web', 'bilibi-installer'):
                    (target / name).write_bytes(('pinned build ' + name).encode())
                self.assertEqual(self.invoke(harness, env, '--record-build').returncode, 0)
                self.assertEqual(builder.qualified_toolchain(config, target)['node'], 'v24.6.0')
                (target / 'bilibi-installer').write_bytes(b'different compiled bytes')
                with self.assertRaisesRegex(ValueError, 'bytes differ'):
                    builder.qualified_toolchain(config, target)

    @unittest.skipUnless(shutil.which('cc') and shutil.which('objdump'), 'ELF tools unavailable')
    def test_checks_both_release_binaries_and_records_real_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binaries = root / 'target/x86_64-unknown-linux-gnu/release'
            binaries.mkdir(parents=True)
            source = root / 'hello.c'
            source.write_text('int main(void) { return 0; }\n')
            for name in ('bilibi-web', 'bilibi-installer'):
                subprocess.run(['cc', str(source), '-o', str(binaries / name)], check=True)
            # A permissive baseline verifies reporting on any native Linux host.
            old_build = builder.BUILD
            builder.BUILD = root
            try:
                with mock.patch.dict(os.environ, {'CARGO_TARGET_DIR': str(root / 'target')}):
                    libraries = builder.verify_binaries({'target': 'x86_64-unknown-linux-gnu', 'glibc': '99.0'})
                saved = json.loads((root / 'evidence/binary-libraries.json').read_text())
                self.assertEqual(saved, libraries)
                self.assertEqual(set(saved), {'bilibi-web', 'bilibi-installer'})
                self.assertIn('NEEDED', saved['bilibi-web']['dynamic_dependencies'])
                with self.assertRaisesRegex(ValueError, 'requires glibc .*baseline is 2.0'):
                    builder.binary_libraries(binaries / 'bilibi-web', '2.0')
            finally:
                builder.BUILD = old_build


if __name__ == '__main__':
    unittest.main()
