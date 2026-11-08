"""Run the prerequisite gate with PostgreSQL outside Debian's versioned path."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts/dev-setup.sh'


class DevSetupTest(unittest.TestCase):
    def test_ready_uses_pg_config_for_server_binaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = root / 'bin'
            tools.mkdir()
            server = root / 'postgres'
            server.mkdir()
            setup = root / 'platform/scripts'
            setup.mkdir(parents=True)
            (setup / 'setup.sh').write_text('echo "setup should be unnecessary" >&2; exit 97\n')
            commands = {name: 'exit 0' for name in ('git', 'curl', 'ss', 'flock', 'psql', 'pg_isready',
                                                   'npm', 'node', 'rustup', 'cargo', 'rustc')}
            commands['pg_config'] = f'echo "{server}"'
            for name, body in commands.items():
                path = tools / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            for name in ('initdb', 'pg_ctl'):
                path = server / name
                path.touch(mode=0o755)
            (tools / 'python3').symlink_to(shutil.which('python3'))
            (tools / 'bash').symlink_to(shutil.which('bash'))
            (tools / 'dirname').symlink_to(shutil.which('dirname'))
            env = {**os.environ, 'PATH': str(tools), 'HOME': str(root),
                   'DATABASE_URL': '', 'PG18_BIN': ''}
            result = subprocess.run([shutil.which('bash'), str(SCRIPT), str(setup.parent)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
