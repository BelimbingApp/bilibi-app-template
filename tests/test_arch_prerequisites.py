"""Exercise Arch package installation and the PostgreSQL version refusal."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import arch_prerequisites as arch


class ArchPrerequisitesTest(unittest.TestCase):
    def test_detects_arch_and_derivatives_from_os_release(self):
        for release, expected in [({'ID': 'arch'}, True),
                                  ({'ID': 'omarchy', 'ID_LIKE': 'arch linux'}, True),
                                  ({'ID': 'ubuntu', 'ID_LIKE': 'debian'}, False),
                                  ({'ID': 'other', 'ID_LIKE': 'archlinux'}, False)]:
            with self.subTest(release=release), mock.patch.object(
                    arch.platform, 'freedesktop_os_release', return_value=release):
                self.assertEqual(arch.is_arch(), expected)

    def tools(self, root, postgres='18.6'):
        commands = {
            'pacman': '''printf '%s\\n' "$*" >>"$PACKAGE_LOG"
case "$1" in
  -Q) exit "${PACKAGE_MISSING:-1}" ;;
  -Si) echo "Version : ${PACKAGE_VERSION:-18.6-4}" ;;
esac''',
            'postgres': f'echo "postgres (PostgreSQL) {postgres}"',
        }
        for name, body in commands.items():
            command = root / name
            command.write_text('#!/bin/sh\n' + body + '\n')
            command.chmod(0o755)

    def test_installs_missing_packages_after_full_upgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.tools(root)
            log = root / 'packages.log'
            with mock.patch.dict(os.environ, {'PATH': str(root), 'PACKAGE_LOG': str(log)}), \
                    mock.patch.object(arch, 'is_arch', return_value=True), \
                    mock.patch.object(arch.os, 'geteuid', return_value=0):
                arch.prepare('18')
            transactions = [line for line in log.read_text().splitlines() if not line.startswith('-Q')]
            self.assertEqual(transactions[0], '-Si postgresql')
            self.assertTrue(transactions[1].startswith('-Syu --needed --noconfirm '))
            self.assertIn('rustup', transactions[1].split())
            self.assertIn('python-pip', transactions[1].split())
            self.assertIn('postgresql=18.6-4', transactions[1].split())

    def test_wrong_postgresql_stops_before_mutating_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.tools(root, postgres='19.0')
            log = root / 'packages.log'
            with mock.patch.dict(os.environ, {'PATH': str(root), 'PACKAGE_LOG': str(log)}), \
                    mock.patch.object(arch, 'is_arch', return_value=True):
                with self.assertRaisesRegex(ValueError, 'PostgreSQL is 19.0; this recipe needs 18'):
                    arch.prepare('18')
            self.assertTrue(all(line.startswith('-Q ') for line in log.read_text().splitlines()))

    def test_installed_packages_need_no_privileges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.tools(root)
            log = root / 'packages.log'
            with mock.patch.dict(os.environ, {'PATH': str(root), 'PACKAGE_LOG': str(log), 'PACKAGE_MISSING': '0'}), \
                    mock.patch.object(arch, 'is_arch', return_value=True), \
                    mock.patch.object(arch.os, 'geteuid', return_value=1000):
                arch.prepare('18')
            self.assertTrue(all(line.startswith('-Q ') for line in log.read_text().splitlines()))

    def test_wrong_repository_major_stops_before_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.tools(root)
            log = root / 'packages.log'
            with mock.patch.dict(os.environ, {'PATH': str(root), 'PACKAGE_LOG': str(log), 'PACKAGE_VERSION': '19.0-1'}), \
                    mock.patch.object(arch, 'is_arch', return_value=True), \
                    mock.patch.object(arch.os, 'geteuid', return_value=0):
                with self.assertRaisesRegex(ValueError, 'PostgreSQL is 19.0-1; this recipe needs 18'):
                    arch.prepare('18')
            self.assertFalse(any(line.startswith('-Syu ') for line in log.read_text().splitlines()))

    def test_debian_does_not_invoke_pacman(self):
        with mock.patch.object(arch, 'is_arch', return_value=False), \
                mock.patch.object(arch.subprocess, 'run', side_effect=AssertionError('unexpected package command')):
            arch.prepare('18')


if __name__ == '__main__':
    unittest.main()
