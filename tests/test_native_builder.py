"""Exercise the native tool gate and inspect actual ELF executables."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts/native_builder.py'
spec = importlib.util.spec_from_file_location('native_builder', SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NativeBuilderTest(unittest.TestCase):
    def test_setup_refuses_a_node_version_mismatch_before_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = root / 'bin'
            tools.mkdir()
            commands = {'rustup': 'echo 1.99.0', 'rustc': 'echo "rustc 1.99.0"',
                        'cargo': 'echo "cargo 1.99.0"', 'node': 'echo v22.0.0'}
            for name in ('git', 'curl', 'objdump', 'cc', 'psql'):
                commands[name] = 'exit 0'
            for name, body in commands.items():
                path = tools / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            result = subprocess.run([shutil.which('python3'), str(SCRIPT), '--setup'],
                                    env={**os.environ, 'PATH': str(tools), 'BILIBI_BUILD_DIR': str(root / 'build')},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Node is v22.0.0; this recipe needs v24.6.0.', result.stderr)
            self.assertFalse((root / 'build/native-tools/python').exists())

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
