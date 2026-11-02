import base64
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('publish', ROOT / 'scripts/publish.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)

FAKE_GH_AXI = '''#!/usr/bin/env python3
import base64, json, os, sys
path = sys.argv[2]
releases = json.load(open(os.environ['FAKE_RELEASES']))
if '/releases/tags/' in path:
    sys.exit(1)
page = int(path.split('&page=')[1])
body = releases[(page - 1) * 100:page * 100]
print('  body: "' + base64.b64encode(json.dumps(body).encode()).decode() + '"')
'''


class FindReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tool = Path(self.tmp.name) / 'gh-axi'
        tool.write_text(FAKE_GH_AXI)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        self.releases = Path(self.tmp.name) / 'releases.json'
        old = {key: os.environ.get(key) for key in ('PATH', 'FAKE_RELEASES')}
        os.environ['PATH'] = self.tmp.name + os.pathsep + old['PATH']
        os.environ['FAKE_RELEASES'] = str(self.releases)
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in old.items()])

    def write(self, releases):
        self.releases.write_text(json.dumps(releases))

    def test_finds_a_draft_release(self):
        self.write([{'tag_name': 'setup-v1.0.0', 'draft': True, 'assets': []}])
        self.assertTrue(publish.find_release('o/r', 'setup-v1.0.0')['draft'])

    def test_finds_release_beyond_the_first_page(self):
        others = [{'tag_name': f'setup-v0.0.{i}', 'draft': False} for i in range(100)]
        self.write(others + [{'tag_name': 'setup-v1.0.0', 'draft': True}])
        self.assertEqual(publish.find_release('o/r', 'setup-v1.0.0')['tag_name'], 'setup-v1.0.0')

    def test_refuses_missing_or_ambiguous_release(self):
        self.write([{'tag_name': 'setup-v1.0.1', 'draft': True}])
        with self.assertRaises(ValueError):
            publish.find_release('o/r', 'setup-v1.0.0')
        self.write([{'tag_name': 'setup-v1.0.0', 'draft': True}, {'tag_name': 'setup-v1.0.0', 'draft': False}])
        with self.assertRaises(ValueError):
            publish.find_release('o/r', 'setup-v1.0.0')


if __name__ == '__main__':
    unittest.main()


class StartReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = Path(self.tmp.name) / 'remote.git'
        self.work = Path(self.tmp.name) / 'work'
        subprocess.run(['git', 'init', '-q', '--bare', str(self.remote)], check=True)
        subprocess.run(['git', 'init', '-q', str(self.work)], check=True)
        self.git('config', 'user.email', 'a@example.test')
        self.git('config', 'user.name', 'A')
        self.git('commit', '-q', '--allow-empty', '-m', 'First')
        self.git('remote', 'add', 'origin', str(self.remote))
        branch = self.git('branch', '--show-current').strip()
        self.git('push', '-q', '-u', 'origin', branch)
        subprocess.run(['git', 'symbolic-ref', 'HEAD', f'refs/heads/{branch}'], cwd=self.remote, check=True)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.work, text=True, stdout=subprocess.PIPE, check=True).stdout

    def test_pushes_the_new_tag(self):
        publish.start_release('setup-v1.0.0', self.work)
        self.assertIn('setup-v1.0.0', self.git('ls-remote', '--tags', 'origin'))

    def test_failed_push_removes_the_local_tag_so_the_release_can_be_retried(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
        with self.assertRaisesRegex(ValueError, 'was not published'):
            publish.start_release('setup-v1.0.0', self.work)
        self.assertEqual(self.git('tag', '--list'), '')
        hook.unlink()
        publish.check_new_tag('setup-v1.0.0', self.work)
        publish.start_release('setup-v1.0.0', self.work)
        self.assertIn('setup-v1.0.0', self.git('ls-remote', '--tags', 'origin'))

    def test_refuses_a_commit_not_on_the_default_branch(self):
        self.git('checkout', '-q', '-b', 'unreviewed')
        self.git('commit', '-q', '--allow-empty', '-m', 'Unreviewed')
        with self.assertRaisesRegex(ValueError, 'reviewed commit on the default branch'):
            publish.start_release('setup-v1.0.0', self.work)
        self.assertEqual(self.git('tag', '--list'), '')
