"""Build records use real Git remotes; fixtures replace only tool installation and compilation."""
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import time
import tomllib
import unittest

from test_workspace_update import WorkspaceFixture

RECORD_FILES = ('setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock')

# Run real recipe restore/verify commands while replacing the large Rust build.
BUILD = '''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/recipe.py restore
python3 scripts/recipe.py verify
python3 - <<'BUILD_RECORD'
import json
import os
from pathlib import Path
import tomllib
root = Path.cwd()
output = Path(os.environ['BILIBI_BUILD_DIR'])
output.mkdir(parents=True, exist_ok=True)
(output / 'built.json').write_text(json.dumps(tomllib.loads((root / 'setup.toml').read_text())))
BUILD_RECORD
'''
BUILDER = '''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $1 == --development ]] && shift
if [[ $1 == python3 ]]; then
    # Replace cargo metadata and Platform discovery --pin with the same record
    # shape. The wrapper still fetches, validates and installs the candidate.
    python3 scripts/test-record.py
else
    exec bash "$@"
fi
'''
RECORD = '''import hashlib
import subprocess
from pathlib import Path
import tomllib
root = Path.cwd()
setup = tomllib.loads((root / 'setup.toml').read_text())
lock = ('# New lock for ' + str(setup) + '\\n').encode()
(root / 'composition/Cargo.lock').write_bytes(lock)
lines = [f'platform = "{setup["platform"]["commit"]}"',
         f'cargo_lock_sha256 = "{hashlib.sha256(lock).hexdigest()}"']
for layer in ('domains', 'extensions'):
    for part in setup.get(layer, []):
        lines += [f'[containers."{layer}/{part["id"]}"]', f'head = "{part["commit"]}"']
(root / 'composition/manifest.txt').write_text('\\n'.join(lines) + '\\n')
subprocess.run(['python3', 'scripts/recipe.py', 'restore'], check=True)
'''


class BuildFixture(WorkspaceFixture):
    """An application whose check runs real recipe commands and a stand-in build."""

    def setUp(self):
        super().setUp()
        self.env.pop('CI', None)
        self.env.pop('GITHUB_ACTIONS', None)
        for name in ('builder.toml', 'rust-toolchain.toml'):
            (self.app / name).write_text('# fixture\n')
        for name in ('build.sh', 'check.sh'):
            (self.app / 'scripts' / name).write_text(BUILD)
        (self.app / 'scripts/builder.sh').write_text(BUILDER)
        (self.app / 'scripts/test-record.py').write_text(RECORD)
        self.commit(self.app, 'Build fixture')
        self.initial = self.record()

    def record(self):
        return {name: (self.app / name).read_bytes() for name in RECORD_FILES}

    def saved(self):
        return tomllib.loads((self.app / 'setup.toml').read_text())

    def built(self):
        return json.loads((self.app / 'build/built.json').read_text())

    def move(self, folder, push=True):
        self.git(folder, 'checkout', '-b', 'new-work')
        self.git(folder, 'commit', '--allow-empty', '-m', 'New work')
        if push:
            self.git(folder, 'push', 'origin', 'HEAD:refs/heads/new-work')
        return self.git(folder, 'rev-parse', 'HEAD')

    def terminal(self, command, answers, prompt=b'Build with them? (y/n) ', ok=True, args=()):
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen([str(self.app / 'bilibi'), command, *args], env=self.env,
                                       cwd=self.root, stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            slave = None
            output = b''
            count = 0
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                    prompts = output.count(prompt)
                    while count < prompts and count < len(answers):
                        os.write(master, (answers[count] + '\n').encode())
                        count += 1
                elif process.poll() is not None:
                    break
            if process.poll() is None:
                process.kill()
            process.wait()
            self.assertEqual(process.returncode == 0, ok, output.decode())
            return output.decode()
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)


class BuildRecordTest(BuildFixture):
    def test_build_yes_saves_all_three_files_and_builds_new_commits(self):
        head = self.move(self.domain)
        before = self.snapshot()
        output = self.terminal('check', ['y'])
        self.assertIn(f'finance has new commits since the last build ({self.domain_first[:7]} -> {head[:7]}). Build with them? (y/n)', output)
        self.assertEqual(self.saved()['domains'][0]['commit'], head)
        self.assertEqual(self.built()['domains'][0]['commit'], head)
        self.assertTrue(all(self.record()[name] != self.initial[name] for name in RECORD_FILES))
        self.assertEqual(before[1:], self.snapshot()[1:])
        self.assertEqual(self.git(self.domain, 'branch', '--show-current'), 'new-work')

    def test_no_builds_saved_versions_without_switching_local_branches(self):
        self.move(self.domain)
        before = self.snapshot()
        self.terminal('check', ['n'])
        self.assertEqual(self.built()['domains'][0]['commit'], self.domain_first)
        self.assertEqual(self.record(), self.initial)
        self.assertEqual(self.snapshot(), before)

    def test_parts_can_be_accepted_and_declined_separately(self):
        self.move(self.platform)
        domain = self.move(self.domain)
        output = self.terminal('check', ['n', 'y'])
        self.assertIn('Platform has new commits', output)
        self.assertEqual(self.built()['platform']['commit'], self.platform_first)
        self.assertEqual(self.built()['domains'][0]['commit'], domain)
        self.assertEqual(self.saved(), self.built())

    def test_no_keyboard_requires_yes_and_yes_accepts_on_check(self):
        head = self.move(self.domain)
        self.assertIn('--yes', self.command('check', ok=False))
        self.assertEqual(self.record(), self.initial)
        self.command('check', '--yes')
        self.assertEqual(self.built()['domains'][0]['commit'], head)

    def test_unchanged_build_needs_no_keyboard_and_keeps_lock(self):
        self.command('check')
        self.assertEqual(self.record(), self.initial)
        self.assertEqual(self.built()['domains'][0]['commit'], self.domain_first)

    def test_leftover_candidate_neither_blocks_nor_is_removed(self):
        leftover = self.app / 'build-recipe'
        leftover.mkdir()
        (leftover / 'marker').write_text('other workspace')
        self.command('check')
        self.assertEqual(self.built()['domains'][0]['commit'], self.domain_first)
        self.assertEqual((leftover / 'marker').read_text(), 'other workspace')
        self.assertEqual(sorted(p.name for p in self.app.glob('build-recipe*')), ['build-recipe'])

    def test_dirty_sources_are_refused_even_with_yes(self):
        for folder, path, name in ((self.domain, 'notes', 'finance'), (self.platform, 'notes', 'Platform'),
                                   (self.platform, 'Cargo.lock', 'Platform')):
            with self.subTest(folder=folder, path=path):
                target = folder / path
                before = target.read_bytes()
                target.write_bytes(before + b'Uncommitted edits\n')
                snapshot = self.snapshot()
                self.assertIn(f"{name} has changes that aren't committed. Commit and push them first.",
                              self.command('check', '--yes', ok=False))
                self.assertEqual(self.record(), self.initial)
                self.assertEqual(self.snapshot(), snapshot)
                target.write_bytes(before)
        (self.domain / 'untracked').write_text('work')
        self.assertIn("finance has changes that aren't committed", self.command('check', '--yes', ok=False))

    def test_unpushed_or_unfetchable_commits_are_refused(self):
        self.move(self.domain, push=False)
        before = self.snapshot()
        self.assertIn('finance has commits that are not pushed or cannot be fetched from origin.',
                      self.command('check', '--yes', ok=False))
        self.assertEqual(self.record(), self.initial)
        self.assertEqual(self.snapshot(), before)

    def test_other_ci_requires_yes_and_accepts_pushed_commits(self):
        head = self.move(self.domain)
        self.env['CI'] = 'true'
        self.assertIn('--yes', self.command('check', ok=False))
        self.command('check', '--yes')
        self.assertEqual(self.built()['domains'][0]['commit'], head)

    def test_failed_candidate_keeps_previous_record(self):
        self.move(self.domain)
        (self.app / 'scripts/test-record.py').write_text('raise SystemExit("Candidate failed")\n')
        self.assertIn('Candidate failed', self.command('check', '--yes', ok=False))
        self.assertEqual(self.record(), self.initial)

    def test_added_extension_can_be_declined_then_saved(self):
        extension = self.platform / 'apps/extensions/extra'
        self.git(self.platform, 'clone', 'https://example.test/finance.git', str(extension))
        with (self.platform / '.git/info/exclude').open('a') as stream:
            stream.write('/apps/extensions/extra/\n')
        output = self.terminal('check', ['n'])
        self.assertIn(f'extra has new commits since the last build (not included -> {self.domain_first[:7]})', output)
        self.assertNotIn('extensions', self.built())
        self.assertTrue(extension.exists())
        self.command('check', '--yes')
        self.assertEqual(self.built()['extensions'][0]['id'], 'extra')
        self.assertEqual(self.built()['extensions'][0]['commit'], self.domain_first)

    def test_tag_only_source_commit_can_be_recorded(self):
        head = self.move(self.domain, push=False)
        self.git(self.domain, 'push', 'origin', 'HEAD:refs/tags/component-release')
        self.command('check', '--yes')
        self.assertEqual(self.built()['domains'][0]['commit'], head)

    def test_staged_source_edits_are_refused(self):
        (self.domain / 'notes').write_text('Staged changes\n')
        self.git(self.domain, 'add', 'notes')
        before = self.snapshot()
        self.assertIn("finance has changes that aren't committed", self.command('check', '--yes', ok=False))
        self.assertEqual(self.snapshot(), before)

    def test_failed_check_keeps_previous_record_untouched(self):
        self.move(self.domain)
        (self.app / 'scripts/check.sh').write_text('exit 1\n')
        self.command('check', '--yes', ok=False)
        self.assertEqual(self.record(), self.initial)

    def test_status_has_one_notice_for_moved_parts(self):
        self.move(self.domain)
        output = self.command('status')
        self.assertEqual(output.count('finance has new commits since the last build.'), 1)

    def test_help_lists_release_and_yes(self):
        help_text = self.command('help')
        self.assertIn('release --tag setup-vX.Y.Z --yes', help_text)
        self.assertIn('check --yes', help_text)
        self.assertNotIn('build --yes', help_text)
        self.assertNotIn('  build ', help_text)
        for args in (['--help'], ['build'], ['build', '--yes']):
            result = subprocess.run([str(self.app / 'bilibi'), *args], env=self.env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0 if args == ['--help'] else 2)
            self.assertNotIn('check,build', result.stdout + result.stderr)
        self.assertFalse((self.app / '.local/successful-check').exists())


if __name__ == '__main__':
    unittest.main()
