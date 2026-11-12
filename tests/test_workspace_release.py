"""./bilibi release with real Git remotes; only GitHub's API (gh-axi) is a recorded stand-in."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from test_workspace_build import RECORD_FILES, BuildFixture

APP_URL = 'https://github.com/acme/shop.git'
TARGET = 'x86_64-unknown-linux-gnu'
CHECK_RUNS = '/actions/workflows/check.yml/runs'
RELEASE_RUNS = '/actions/workflows/release.yml/runs'

# Answers the GitHub calls ./bilibi release makes, from state.json beside it.
# "builds" holds the successive answers about the Release setup run; the last repeats.
FAKE_GH_AXI = r'''#!/usr/bin/env python3
import base64, hashlib, json, shutil, subprocess, sys
from pathlib import Path
home = Path(__file__).resolve().parent
state_path = home / 'state.json'
state = json.loads(state_path.read_text())
with (home / 'calls').open('a') as log:
    log.write(' '.join(sys.argv[1:]) + '\n')

def answer(body):
    print('  body: "' + base64.b64encode(json.dumps(body).encode()).decode() + '"')

def assets():
    return [{'name': path.name, 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(Path(state['assets']).iterdir())]

if sys.argv[1] == 'api':
    path = sys.argv[2]
    if '/check.yml/runs' in path:
        answer({'workflow_runs': state['checks']})
    elif '/release.yml/runs' in path:
        polls = state['polls'] = state.get('polls', 0) + 1
        builds = state['builds']
        answer({'workflow_runs': builds[min(polls, len(builds)) - 1]})
    elif '/commits/' in path:
        tag = path.rsplit('/', 1)[1]
        answer({'sha': subprocess.check_output(['git', '--git-dir', state['remote'], 'rev-parse', tag + '^{commit}'],
                                               text=True).strip()})
    elif path.endswith('/immutable-releases'):
        answer({'enabled': state['immutable']})
    elif '/releases?' in path:
        answer([{**release, 'assets': assets()} for release in state['releases']] if '&page=1' in path else [])
    else:
        sys.exit(f'unexpected api call {path}')
elif sys.argv[1:3] == ['release', 'download']:
    folder = Path(sys.argv[sys.argv.index('--dir') + 1])
    for path in Path(state['assets']).iterdir():
        shutil.copy2(path, folder / path.name)
elif sys.argv[1:3] == ['release', 'edit']:
    for release in state['releases']:
        release.update(draft=False, immutable=True)
else:
    sys.exit(f'unexpected gh-axi call {sys.argv[1:]}')
state_path.write_text(json.dumps(state))
'''


class ReleaseTest(BuildFixture):
    def setUp(self):
        super().setUp()
        # The application's origin is its own GitHub repository, served from application.git.
        (self.root / 'shop.git').symlink_to(self.root / 'application.git')
        self.git(self.app, 'remote', 'set-url', 'origin', APP_URL)
        self.git(self.app, 'push', 'origin', 'HEAD:main')
        self.command('check')
        # The locked helper as release installs it, with gh-axi replaced by the stand-in.
        helper = self.app / '.local/github-tools'
        (helper / 'node_modules/.bin').mkdir(parents=True)
        shutil.copy2(self.app / 'scripts/github-tools/package-lock.json', helper)
        self.fake = helper / 'node_modules/.bin/gh-axi'
        self.fake.write_text(FAKE_GH_AXI)
        self.fake.chmod(0o755)
        self.env['BILIBI_RELEASE_POLL_SECONDS'] = '0'
        self.head = self.git(self.app, 'rev-parse', 'HEAD')
        self.github(checks=[self.run_state('completed', 'success', 'check/1')],
                    builds=[[], [self.run_state('in_progress', None)], [self.run_state('completed', 'success')]],
                    immutable=True, remote=str(self.root / 'application.git'))

    def run_state(self, status, conclusion, name='release/7'):
        return {'status': status, 'conclusion': conclusion, 'head_sha': self.head,
                'html_url': f'https://github.com/acme/shop/actions/runs/{name}'}

    def github(self, **changes):
        path = self.fake.parent / 'state.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**state, **changes}))

    def calls(self):
        path = self.fake.parent / 'calls'
        return path.read_text() if path.exists() else ''

    def built_release(self, tag):
        """The assets Release setup would have attached to the draft for tag."""
        assets = self.root / f'assets-{tag}'
        assets.mkdir()
        files = {f'bilibi-{tag}-{TARGET}.tar.gz': b'application archive\n', f'bilibi-installer-{TARGET}': b'installer\n',
                 'bilibi-server-tools.tar.gz': b'server tools\n', 'builder-packages.txt': b'packages\n',
                 'publication.json': b'{}\n',
                 'recipe.json': json.dumps({'setup': {'repository': 'acme/shop', 'tag': tag, 'commit': self.head},
                                            'built_for': {'target': TARGET}}).encode()}
        for name, data in files.items():
            (assets / name).write_bytes(data)
        (assets / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                                                   for name, data in sorted(files.items())))
        self.github(assets=str(assets), polls=0, releases=[{
            'tag_name': tag, 'draft': True, 'html_url': f'https://github.com/acme/shop/releases/tag/{tag}'}])
        return assets / f'bilibi-{tag}-{TARGET}.tar.gz'

    def remote_tags(self):
        return self.git(self.root / 'application.git', 'tag', '--list')

    def test_suggests_the_first_version_then_publishes_and_says_what_to_upload(self):
        self.built_release('setup-v1.0.0')
        output = self.terminal('release', [''], prompt=b'or type no to stop: ')
        self.assertIn('Check setup passed on main', output)
        self.assertIn('This is the first release on origin.', output)
        self.assertIn(f'Release commit {self.head[:7]} as setup-v1.0.0?', output)
        self.assertIn('https://github.com/acme/shop/actions/runs/release/7', output)
        self.assertEqual(self.git(self.root / 'application.git', 'rev-parse', 'setup-v1.0.0'), self.head)
        self.assertIn('release edit setup-v1.0.0 --repo acme/shop --draft=false', self.calls())
        download = self.app / '.local/releases/setup-v1.0.0' / f'bilibi-setup-v1.0.0-{TARGET}.tar.gz'
        self.assertEqual(download.read_bytes(), b'application archive\n')
        self.assertIn('Released setup-v1.0.0: https://github.com/acme/shop/releases/tag/setup-v1.0.0', output)
        self.assertIn('Set GitHub repository to acme/shop', output)
        self.assertIn(f'Open Software -> Releases and upload this file:\r\n       {download}', output)
        self.assertIn(hashlib.sha256(b'application archive\n').hexdigest(), output)
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), '')
        self.assertEqual(set(self.git(self.app, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()),
                         {'builder.toml', 'rust-toolchain.toml', 'scripts/build.sh', 'scripts/builder.sh',
                          'scripts/check.sh', 'scripts/test-record.py'})
        # Running it again only downloads and checks the published release.
        output = self.command('release', '--tag', 'setup-v1.0.0')
        self.assertIn('already on origin for this commit; continuing', output)
        self.assertEqual(self.calls().count('release edit'), 1)
        self.assertIn('Open Software -> Releases', output)

    def test_suggestion_follows_the_newest_release_and_a_typed_version_replaces_it(self):
        for tag in ('setup-v1.2.3', 'setup-v1.10.0', 'setup-v9.0.0-test'):
            self.git(self.author, 'push', 'origin', f'HEAD:refs/tags/{tag}')
        before = self.remote_tags()
        output = self.terminal('release', ['no'], prompt=b'or type no to stop: ', ok=False)
        self.assertIn('The last release on origin is setup-v1.10.0.', output)
        self.assertIn('as setup-v1.10.1?', output)
        self.assertIn('Nothing was tagged or pushed.', output)
        self.assertEqual(self.remote_tags(), before)
        self.built_release('setup-v2.0.0')
        output = self.terminal('release', ['v2', 'setup-v2.0.0'], prompt=b'or type no to stop: ')
        self.assertIn('Use setup-vX.Y.Z', output)
        self.assertIn('setup-v2.0.0', self.remote_tags())
        self.assertNotIn('setup-v1.10.1', self.remote_tags())

    def test_without_a_terminal_release_needs_tag_and_yes(self):
        self.assertIn('./bilibi release --tag setup-v1.0.0 --yes', self.command('release', ok=False))
        result = subprocess.run([str(self.app / 'bilibi'), 'release', '--yes'], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--tag setup-vX.Y.Z --yes', result.stderr)
        self.assertEqual(self.remote_tags(), '')

    def test_refuses_while_the_last_check_on_main_failed_or_is_running(self):
        for status, conclusion, message in (('completed', 'failure', 'did not pass (failure)'),
                                            ('in_progress', None, 'still running on main')):
            with self.subTest(status=status):
                self.github(checks=[self.run_state(status, conclusion, 'check/2')])
                output = self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False)
                self.assertIn(message, output)
                self.assertIn('https://github.com/acme/shop/actions/runs/check/2', output)
                self.assertEqual(self.remote_tags(), '')

    def test_refuses_when_the_newest_check_is_for_an_older_commit(self):
        self.github(checks=[{**self.run_state('completed', 'success', 'check/3'), 'head_sha': 'a' * 40}])
        output = self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False)
        self.assertIn('has not run on the newest main commit yet', output)
        self.assertEqual(self.remote_tags(), '')

    def test_failed_build_explains_the_rerun_and_the_same_command_continues(self):
        self.built_release('setup-v1.0.0')
        self.github(builds=[[self.run_state('completed', 'failure')]])
        output = self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False)
        self.assertIn('Release setup did not pass (failure)', output)
        self.assertIn('Re-run all jobs', output)
        self.assertNotIn('release edit', self.calls())
        self.git(self.app, 'tag', '-d', 'setup-v1.0.0')
        self.github(polls=0, builds=[[self.run_state('queued', None)], [self.run_state('completed', 'success')]])
        output = self.command('release', '--tag', 'setup-v1.0.0')
        self.assertIn('continuing that release', output)
        self.assertIn('Released setup-v1.0.0', output)

    def test_a_damaged_download_is_not_published(self):
        archive = self.built_release('setup-v1.0.0')
        archive.write_bytes(b'changed after the build\n')
        output = self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False)
        self.assertIn(f'Checksum mismatch: {archive.name}', output)
        self.assertNotIn('release edit', self.calls())

    def test_a_test_version_is_checked_but_stays_a_draft(self):
        self.built_release('setup-v1.2.3-test')
        output = self.command('release', '--tag', 'setup-v1.2.3-test', '--yes')
        self.assertIn('setup-v1.2.3-test is a test build, so it stays a draft', output)
        self.assertNotIn('release edit', self.calls())
        self.assertNotIn('Released setup-v1.2.3-test', output)

    def test_requires_a_successful_check_of_the_current_record(self):
        (self.app / '.local/successful-check').unlink()
        self.assertEqual(self.command('release', ok=False).strip(), 'Run ./bilibi check first.')
        # An old build-only success must not stand in for the combined check.
        (self.app / '.local/successful-build').write_text('legacy success\n')
        self.assertIn('Run ./bilibi check first.', self.command('release', ok=False))
        self.command('check')
        (self.app / 'setup.toml').write_text((self.app / 'setup.toml').read_text() + '# changed after build\n')
        self.assertIn('Commit the successful build record', self.command('release', ok=False))
        self.assertEqual(self.calls(), '')

    def test_requires_the_checked_record_committed_and_on_main(self):
        self.move(self.domain)
        self.command('check', '--yes')
        self.assertIn('Commit the successful build record', self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False))
        self.git(self.app, 'add', *RECORD_FILES)
        self.git(self.app, 'commit', '-m', 'Record reviewed build')
        self.assertIn('reviewed commit on the default branch',
                      self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False))
        self.assertEqual(self.remote_tags(), '')

    def test_refuses_unrelated_staged_unstaged_or_untracked_work(self):
        for mode in ('unstaged', 'staged', 'untracked'):
            with self.subTest(mode=mode):
                path = self.app / ('extra' if mode == 'untracked' else 'notes')
                original = path.read_bytes() if path.exists() else None
                path.write_text('Unrelated changes\n')
                if mode == 'staged':
                    self.git(self.app, 'add', 'notes')
                before = self.snapshot()
                self.assertIn('Commit the successful build record', self.command('release', '--tag', 'setup-v1.2.3', '--yes', ok=False))
                self.assertEqual(self.snapshot(), before)
                self.git(self.app, 'restore', '--staged', 'notes')
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)

    def test_a_version_already_used_for_another_commit_is_refused(self):
        self.git(self.author, 'push', 'origin', 'HEAD:refs/tags/setup-v1.0.0')
        before = self.snapshot()
        self.assertIn('already exists on origin for commit', self.command('release', '--tag', 'setup-v1.0.0', '--yes', ok=False))
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
