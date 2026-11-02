"""Build records use real Git remotes; fixtures replace only Docker and compilation."""
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


class BuildRecordTest(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.env.pop('CI', None)
        self.env.pop('GITHUB_ACTIONS', None)
        for name in ('builder.toml', 'rust-toolchain.toml', '.dockerignore'):
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

    def terminal(self, command, answers):
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen([str(self.app / 'bilibi'), command], env=self.env,
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
                    prompts = output.count(b'Build with them? (y/n) ')
                    while count < prompts and count < len(answers):
                        os.write(master, (answers[count] + '\n').encode())
                        count += 1
                elif process.poll() is not None:
                    break
            if process.poll() is None:
                process.kill()
            process.wait()
            self.assertEqual(process.returncode, 0, output.decode())
            return output.decode()
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_build_yes_saves_all_three_files_and_builds_new_commits(self):
        head = self.move(self.domain)
        before = self.snapshot()
        output = self.terminal('build', ['y'])
        self.assertIn(f'finance has new commits since the last build ({self.domain_first[:7]} -> {head[:7]}). Build with them? (y/n)', output)
        self.assertEqual(self.saved()['domains'][0]['commit'], head)
        self.assertEqual(self.built()['domains'][0]['commit'], head)
        self.assertTrue(all(self.record()[name] != self.initial[name] for name in RECORD_FILES))
        self.assertEqual(before[1:], self.snapshot()[1:])
        self.assertEqual(self.git(self.domain, 'branch', '--show-current'), 'new-work')

    def test_no_builds_saved_versions_without_switching_local_branches(self):
        self.move(self.domain)
        before = self.snapshot()
        self.terminal('build', ['n'])
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
        self.command('build')
        self.assertEqual(self.record(), self.initial)
        self.assertEqual(self.built()['domains'][0]['commit'], self.domain_first)

    def test_dirty_sources_are_refused_even_with_yes(self):
        for folder, path, name in ((self.domain, 'notes', 'finance'), (self.platform, 'notes', 'Platform'),
                                   (self.platform, 'Cargo.lock', 'Platform')):
            with self.subTest(folder=folder, path=path):
                target = folder / path
                before = target.read_bytes()
                target.write_bytes(before + b'Uncommitted edits\n')
                snapshot = self.snapshot()
                self.assertIn(f"{name} has changes that aren't committed. Commit and push them first.",
                              self.command('build', '--yes', ok=False))
                self.assertEqual(self.record(), self.initial)
                self.assertEqual(self.snapshot(), snapshot)
                target.write_bytes(before)
        (self.domain / 'untracked').write_text('work')
        self.assertIn("finance has changes that aren't committed", self.command('check', '--yes', ok=False))

    def test_unpushed_or_unfetchable_commits_are_refused(self):
        self.move(self.domain, push=False)
        before = self.snapshot()
        self.assertIn('finance has commits that are not pushed or cannot be fetched from origin.',
                      self.command('build', '--yes', ok=False))
        self.assertEqual(self.record(), self.initial)
        self.assertEqual(self.snapshot(), before)

    def test_other_ci_requires_yes_and_accepts_pushed_commits(self):
        head = self.move(self.domain)
        self.env['CI'] = 'true'
        self.assertIn('--yes', self.command('build', ok=False))
        self.command('build', '--yes')
        self.assertEqual(self.built()['domains'][0]['commit'], head)

    def test_failed_candidate_keeps_previous_record(self):
        self.move(self.domain)
        (self.app / 'scripts/test-record.py').write_text('raise SystemExit("Candidate failed")\n')
        self.assertIn('Candidate failed', self.command('build', '--yes', ok=False))
        self.assertEqual(self.record(), self.initial)

    def test_added_extension_can_be_declined_then_saved(self):
        extension = self.platform / 'apps/extensions/extra'
        self.git(self.platform, 'clone', 'https://example.test/finance.git', str(extension))
        with (self.platform / '.git/info/exclude').open('a') as stream:
            stream.write('/apps/extensions/extra/\n')
        output = self.terminal('build', ['n'])
        self.assertIn(f'extra has new commits since the last build (not included -> {self.domain_first[:7]})', output)
        self.assertNotIn('extensions', self.built())
        self.assertTrue(extension.exists())
        self.command('build', '--yes')
        self.assertEqual(self.built()['extensions'][0]['id'], 'extra')
        self.assertEqual(self.built()['extensions'][0]['commit'], self.domain_first)

    def test_tag_only_source_commit_can_be_recorded(self):
        head = self.move(self.domain, push=False)
        self.git(self.domain, 'push', 'origin', 'HEAD:refs/tags/component-release')
        self.command('build', '--yes')
        self.assertEqual(self.built()['domains'][0]['commit'], head)

    def test_staged_source_edits_are_refused(self):
        (self.domain / 'notes').write_text('Staged changes\n')
        self.git(self.domain, 'add', 'notes')
        before = self.snapshot()
        self.assertIn("finance has changes that aren't committed", self.command('check', '--yes', ok=False))
        self.assertEqual(self.snapshot(), before)

    def test_failed_build_keeps_previous_record_untouched(self):
        self.move(self.domain)
        (self.app / 'scripts/build.sh').write_text('exit 1\n')
        self.command('build', '--yes', ok=False)
        self.assertEqual(self.record(), self.initial)

    def test_failed_check_keeps_previous_record_untouched(self):
        self.move(self.domain)
        (self.app / 'scripts/check.sh').write_text('exit 1\n')
        self.command('check', '--yes', ok=False)
        self.assertEqual(self.record(), self.initial)

    def test_status_has_one_notice_for_moved_parts(self):
        self.move(self.domain)
        output = self.command('status')
        self.assertEqual(output.count('finance has new commits since the last build.'), 1)

    def test_release_requires_successful_build_for_current_record(self):
        output = self.command('release', '--tag', 'setup-v1.2.3-test', ok=False)
        self.assertEqual(output.strip(), 'Run ./bilibi build first.')
        self.command('build')
        (self.app / 'setup.toml').write_text((self.app / 'setup.toml').read_text() + '# changed after build\n')
        output = self.command('release', '--tag', 'setup-v1.2.3-test', ok=False)
        self.assertIn('Commit the successful build record', output)

    def test_release_requires_reviewed_record_and_pushes_existing_workflow_tag(self):
        head = self.move(self.domain)
        self.command('build', '--yes')
        record = self.record()
        self.assertIn('Commit the successful build record',
                      self.command('release', '--tag', 'setup-v1.2.3-test', ok=False))
        self.git(self.app, 'add', *RECORD_FILES)
        self.git(self.app, 'commit', '-m', 'Record reviewed build')
        self.git(self.app, 'push', 'origin', 'HEAD:main')
        output = self.command('release', '--tag', 'setup-v1.2.3-test')
        self.assertNotIn(f'finance: {self.domain_first[:7]} -> {head[:7]}', output)
        self.assertEqual(set(self.git(self.app, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()), set(RECORD_FILES))
        self.assertEqual(self.record(), record)
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), '')
        self.assertEqual(self.git(self.root / 'application.git', 'rev-parse', 'refs/tags/setup-v1.2.3-test'),
                         self.git(self.app, 'rev-parse', 'HEAD'))
        self.assertIn('already exists here', self.command('release', '--tag', 'setup-v1.2.3-test', ok=False))

    def test_release_refuses_unrelated_staged_unstaged_or_untracked_work(self):
        self.move(self.domain)
        self.command('build', '--yes')
        for mode in ('unstaged', 'staged', 'staged_then_restored', 'untracked'):
            with self.subTest(mode=mode):
                path = self.app / ('extra' if mode == 'untracked' else 'notes')
                original = path.read_bytes() if path.exists() else None
                path.write_text('Unrelated changes\n')
                if mode in ('staged', 'staged_then_restored'):
                    self.git(self.app, 'add', 'notes')
                if mode == 'staged_then_restored':
                    path.write_bytes(original)
                before = self.snapshot()
                self.assertIn('Commit the successful build record', self.command('release', '--tag', 'setup-v1.2.3', ok=False))
                self.assertEqual(self.snapshot(), before)
                self.git(self.app, 'restore', '--staged', 'notes')
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)

    def test_release_with_committed_record_does_not_make_empty_commit(self):
        self.command('build')
        self.git(self.app, 'push', 'origin', 'HEAD:main')
        before = self.git(self.app, 'rev-parse', 'HEAD')
        self.command('release', '--tag', 'setup-v2.0.0-test')
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), before)

    def test_release_rejects_remote_tag_before_committing(self):
        self.git(self.author, 'tag', 'setup-v1.0.0')
        self.git(self.author, 'push', 'origin', 'refs/tags/setup-v1.0.0')
        self.move(self.domain)
        self.command('build', '--yes')
        self.git(self.app, 'add', *RECORD_FILES)
        self.git(self.app, 'commit', '-m', 'Record reviewed build')
        self.git(self.app, 'push', 'origin', 'HEAD:main')
        before = self.snapshot()
        self.assertIn('already exists on origin', self.command('release', '--tag', 'setup-v1.0.0', ok=False))
        self.assertEqual(self.snapshot(), before)

    def test_help_lists_release_and_yes(self):
        help_text = self.command('help')
        self.assertIn('release --tag setup-vX.Y.Z', help_text)
        self.assertIn('build --yes', help_text)


if __name__ == '__main__':
    unittest.main()
