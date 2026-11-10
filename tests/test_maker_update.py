"""Public CLI proof using real Git remotes and a tiny executable Platform fixture.

Oracle: every command runs the maker commit maker.lock records; only update
moves the record, after listing the maker commits, and the application then
fast-forwards with that maker. Local data and unrelated application files must
survive. A runtime copied from the application or a tampered cache fails.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from test_workspace_update import ROOT, WorkspaceFixture

OFFICIAL = 'https://github.com/BelimbingProject/bilibi-app-maker.git'


class MakerUpdateTest(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.maker = self.root / 'maker-author'
        self.maker.mkdir()
        self.git(self.maker, 'init', '-b', 'main')
        for name in ('scripts',):
            shutil.copytree(ROOT / name, self.maker / name,
                            ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        for name in ('maker-tooling.toml', 'builder.toml', 'rust-toolchain.toml', 'LICENSE'):
            shutil.copy2(ROOT / name, self.maker / name)
        self.commit(self.maker, 'Official fixture tooling one')
        self.first = self.git(self.maker, 'rev-parse', 'HEAD')
        self.git(self.root, 'init', '--bare', str(self.root / 'bilibi-app-maker.git'))
        self.git(self.maker, 'remote', 'add', 'origin', str(self.root / 'bilibi-app-maker.git'))
        self.git(self.maker, 'push', '-u', 'origin', 'main')
        self.git(self.app, 'remote', 'add', 'upstream', OFFICIAL)
        # The fixture already has a legacy copied-tooling app and real saved sources.
        self.command('adopt')
        self.commit(self.app, 'Adopt reviewed bootstrap')
        self.git(self.app, 'push')
        self.git(self.author, 'pull', '--ff-only')
        self.state = self.app / '.git/bilibi-maker'

    def selected(self):
        """The maker.lock in the working tree: what every command runs."""
        return json.loads((self.app / 'maker.lock').read_text())

    def heads(self):
        return [self.git(folder, 'rev-parse', 'HEAD') for folder in (self.app, self.platform, self.domain)]

    def maker_update(self):
        workspace = self.maker / 'scripts/workspace.py'
        workspace.write_text(workspace.read_text().replace('def main():', "def main():\n    print('NEW MAKER RUNTIME', flush=True)"))
        self.commit(self.maker, 'Official fixture tooling two')
        self.second = self.git(self.maker, 'rev-parse', 'HEAD')
        self.git(self.maker, 'push')

    def test_maker_first_then_app_and_daily_idempotence(self):
        preserved = {name: (self.app / name).read_bytes() for name in
                     ('setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock', 'maker.toml')}
        (self.author / 'README.md').write_text('Our own product\n')
        self.upstream()
        # An application-side copied implementation must never win the handoff.
        (self.author / 'scripts/workspace.py').write_text("raise SystemExit('WRONG COPIED RUNTIME')\n")
        self.commit(self.author, 'Frozen legacy copy must not execute')
        self.git(self.author, 'push')
        self.maker_update()
        output = self.command('update')
        self.assertLess(output.index(self.second), output.index('NEW MAKER RUNTIME'))
        self.assertLess(output.index('NEW MAKER RUNTIME'), output.index('Updated application'))
        self.assertEqual(self.selected()['commit'], self.second)
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), self.git(self.author, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.platform, 'rev-parse', 'HEAD'), self.platform_second)
        self.assertEqual(self.git(self.domain, 'rev-parse', 'HEAD'), self.domain_second)
        self.assertEqual((self.app / 'README.md').read_text(), 'Our own product\n')
        self.assertEqual((self.app / 'maker.toml').read_bytes(), preserved['maker.toml'])
        before = self.snapshot()
        marker = self.selected()
        for _ in range(3):
            self.command('update')
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.selected(), marker)
        # The move is an ordinary application change, published through review.
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), 'M maker.lock')
        self.assertEqual(self.git(self.app, 'remote', 'get-url', 'origin'), str(self.root / 'application.git'))

    def test_maker_only_then_app_only_preserves_configuration_and_pins(self):
        (self.author / 'README.md').write_text('Application README\n')
        (self.author / '.env.example').write_text('# App settings\n')
        self.commit(self.author, 'App settings')
        self.git(self.author, 'push')
        self.command('update')
        before = self.heads()
        files = {name: (self.app / name).read_bytes() for name in
                 ('README.md', '.env.example', 'setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock')}
        self.maker_update()
        self.command('update')
        self.assertEqual(before, self.heads())
        for name, data in files.items():
            self.assertEqual((self.app / name).read_bytes(), data)
        self.upstream(components=False)
        self.command('update')
        self.assertEqual(self.selected()['commit'], self.second)
        self.assertEqual(self.git(self.platform, 'rev-parse', 'HEAD'), self.platform_first)
        self.assertEqual(self.git(self.domain, 'rev-parse', 'HEAD'), self.domain_first)

    def test_maker_success_app_failure_reports_installed_identities_and_retry(self):
        self.maker_update()
        (self.app / 'notes').write_text('valuable local work\n')
        before = self.heads()
        output = self.command('update', ok=False)
        self.assertIn('Application update failed; maker.lock above is kept', output)
        self.assertIn(self.second, output)
        self.assertIn(self.git(self.app, 'rev-parse', 'HEAD'), output)
        self.assertEqual(self.selected()['commit'], self.second)
        self.assertEqual(self.heads(), before)
        self.assertEqual((self.app / 'notes').read_text(), 'valuable local work\n')
        (self.app / 'notes').write_text('first application\n')
        self.command('update')

    def test_network_failure_keeps_selection_and_offline_runtime(self):
        self.maker_update()
        helper = self.root / 'git-core/git-remote-https'
        transport = helper.read_bytes()
        helper.write_text('#!/bin/sh\nexit 1\n')
        before = self.snapshot()
        marker = self.selected()
        self.assertIn('Cannot obtain official maker main', self.command('update', ok=False))
        self.assertEqual(self.selected(), marker)
        self.assertEqual(self.snapshot(), before)
        self.assertIn(self.first, self.command('status'))
        helper.write_bytes(transport)
        self.command('update')
        self.assertEqual(self.selected()['commit'], self.second)

    def test_adopted_guard_upgrades_previous_guard_and_checks_commits(self):
        # Persisted state: the exact v2 guard earlier releases wrote for applications.
        hook = self.app / '.git/hooks/pre-commit'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/usr/bin/env bash\n# Bilibi repository guard v2\nset -euo pipefail\n'
                        'root=$(git rev-parse --show-toplevel)\ncd -- "$root"\n'
                        'previous=$(dirname -- "${BASH_SOURCE[0]}")/pre-commit.before-bilibi\n'
                        'if [[ -x $previous ]]; then "$previous" "$@"; fi\n'
                        'exec python3 "$root"/scripts/workspace.py check-staged\n')
        hook.chmod(0o755)
        tools = self.state / self.first / 'scripts'
        subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import workspace; workspace.hooks()',
                        str(tools)], env={**self.env, 'BILIBI_TOOL_APPLICATION_ROOT': str(self.app), 'PYTHONDONTWRITEBYTECODE': '1'},
                       cwd=self.root, check=True, capture_output=True, text=True, timeout=30)
        self.assertFalse((hook.parent / 'pre-commit.before-bilibi').exists())
        # The guard must not depend on copied tooling the adopted application no longer uses.
        shutil.rmtree(self.app / 'scripts')
        self.git(self.app, 'add', '.')
        accepted = subprocess.run(['git', '-C', str(self.app), 'commit', '-m', 'Drop copied tooling'],
                                  env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        (self.app / 'composition/Cargo.lock').write_text('# Tampered lock\n')
        self.git(self.app, 'add', 'composition/Cargo.lock')
        refused = subprocess.run(['git', '-C', str(self.app), 'commit', '-m', 'Inconsistent record'],
                                 env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertNotIn('invalid choice', refused.stderr)
        # Git exports GIT_DIR and GIT_INDEX_FILE to hooks in a linked worktree.
        linked = self.root / 'linked-application'
        self.git(self.app, 'worktree', 'add', '-b', 'linked', str(linked), 'HEAD')
        (linked / 'notes').write_text('linked application work\n')
        self.git(linked, 'add', 'notes')
        accepted = subprocess.run(['git', '-C', str(linked), 'commit', '-m', 'Linked work'],
                                  env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(self.git(self.app, 'remote', 'get-url', 'origin'), str(self.root / 'application.git'))

    def test_cache_tampering_never_executes_and_has_narrow_recovery(self):
        folder = self.state / self.first
        script = folder / 'scripts/workspace.py'
        script.write_text("from pathlib import Path\nPath('UNTRUSTED-EXECUTED').touch()\n")
        before = self.snapshot()
        output = self.command('update', ok=False)
        self.assertIn('changed after download', output)
        # Ordinary commands compare the folder with maker.lock's sha256 and refuse too.
        self.assertIn('does not match the sha256 in maker.lock', self.command('status', ok=False))
        self.assertFalse((self.app / 'UNTRUSTED-EXECUTED').exists())
        self.assertEqual(before, self.snapshot())
        folder.rename(self.state / 'quarantined-cache')
        self.command('update')
        self.assertEqual(self.selected()['commit'], self.first)

    def test_interrupted_install_and_unavailable_revision_retry(self):
        self.maker_update()
        # An interrupted staging directory must not become executable selection.
        pending = self.state / 'install-interrupted/tooling/scripts'
        pending.mkdir(parents=True)
        (pending / 'workspace.py').write_text("raise SystemExit('HALF INSTALL EXECUTED')\n")
        self.command('update')
        self.assertEqual(self.selected()['commit'], self.second)
        self.assertTrue(pending.exists())
        # A selection whose object no longer exists fails without running app code.
        saved = self.selected()
        wrong = {**saved, 'commit': '0' * 40}
        (self.app / 'maker.lock').write_text(json.dumps(wrong))
        before = self.snapshot()
        self.command('status', ok=False)
        self.assertEqual(before, self.snapshot())
        self.command('update')
        self.assertEqual(self.selected(), saved)

    def test_adopted_runtime_preserves_staged_untracked_and_diverged_work(self):
        self.upstream()
        for folder in (self.app, self.platform, self.domain):
            for staged in (False, True):
                with self.subTest(folder=folder, staged=staged):
                    note = folder / 'notes'
                    original = note.read_bytes()
                    note.write_bytes(original + b'valuable change\n')
                    if staged:
                        self.git(folder, 'add', 'notes')
                    before = self.snapshot()
                    self.assertIn('commit or stash it yourself', self.command('update', ok=False))
                    self.assertEqual(before, self.snapshot())
                    if staged:
                        self.git(folder, 'restore', '--staged', 'notes')
                    note.write_bytes(original)
            unknown = folder / 'untracked-work'
            unknown.write_text('preserve me\n')
            before = self.snapshot()
            self.assertIn('uncommitted work', self.command('update', ok=False))
            self.assertEqual(before, self.snapshot())
            self.assertEqual(unknown.read_text(), 'preserve me\n')
            unknown.unlink()
        # Incoming app changes plus a private commit are genuine divergence.
        self.git(self.app, 'commit', '--allow-empty', '-m', 'Private application commit')
        before = self.snapshot()
        self.assertIn('reconcile any divergence yourself', self.command('update', ok=False))
        self.assertEqual(before, self.snapshot())

    def test_shared_build_uses_application_record_and_cached_tools(self):
        from test_workspace_build import BUILD, BUILDER, RECORD
        for name in ('build.sh', 'check.sh'):
            (self.maker / 'scripts' / name).write_text(BUILD)
        (self.maker / 'scripts/builder.sh').write_text(BUILDER)
        (self.maker / 'scripts/test-record.py').write_text(RECORD)
        self.commit(self.maker, 'Replace compiler boundary with lightweight fixture')
        self.git(self.maker, 'push')
        for name in ('builder.toml', 'rust-toolchain.toml'):
            (self.app / name).write_text('# Application-specific configuration\n')
        (self.app / 'scripts/build.sh').write_text('exit 99\n')
        self.commit(self.app, 'Application owns config; copied build must not run')
        self.git(self.app, 'push')
        self.command('update')
        before = (self.app / 'setup.toml').read_bytes()
        lock = (self.app / 'maker.lock').read_bytes()
        self.command('build')
        built = json.loads((self.app / 'build/built.json').read_text())
        self.assertEqual(built, tomllib.loads(before.decode()))
        self.assertEqual((self.app / 'setup.toml').read_bytes(), before)
        self.assertEqual((self.app / 'builder.toml').read_text(), '# Application-specific configuration\n')
        # update moved maker.lock to the newer maker; the build ran it and left the record alone.
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), 'M maker.lock')
        self.assertEqual((self.app / 'maker.lock').read_bytes(), lock)
        self.assertEqual(self.selected()['commit'], self.git(self.maker, 'rev-parse', 'HEAD'))
        # Once committed, another build leaves the checkout clean.
        self.commit(self.app, 'Move maker.lock')
        self.command('build')
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), '')
        self.assertEqual((self.app / 'maker.lock').read_bytes(), lock)

    @unittest.skipUnless(yaml, 'PyYAML reads the workflow definition')
    def test_ci_workflow_runs_recorded_tooling_without_copied_scripts(self):
        recorded = json.loads((self.app / 'maker.lock').read_text())
        self.assertEqual(recorded['commit'], self.first)
        self.maker_update()
        shutil.rmtree(self.app / 'scripts')
        self.commit(self.app, 'Remove frozen copied tooling')
        steps = {step.get('name'): step['run'] for step in
                 yaml.safe_load((ROOT / '.github/workflows/check.yml').read_text())['jobs']['check']['steps'] if 'run' in step}

        def ci(*names, ok=True):
            github_env = self.root / 'github-env'
            github_env.write_text('')
            env = {**self.env, 'GITHUB_ENV': str(github_env)}
            for name in names:
                result = subprocess.run(['bash', '-e', '-c', steps[name]], cwd=self.app, env=env,
                                        capture_output=True, text=True, timeout=60)
                if result.returncode:
                    self.assertFalse(ok, result.stdout + result.stderr)
                    return result.stdout + result.stderr
                env.update(line.split('=', 1) for line in github_env.read_text().splitlines())
            self.assertTrue(ok)
            return env

        env = ci('Install the recorded maker tooling', 'Validate the saved recipe and lock')
        # Oracle: the commit adoption recorded, not newer main or the application's copies.
        self.assertEqual(Path(env['BILIBI_TOOLS']), self.state / self.first)
        self.assertEqual(env['BILIBI_TOOL_APPLICATION_ROOT'], str(self.app))
        self.assertEqual(self.selected()['commit'], self.first)
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), '')
        (self.app / 'maker.lock').write_text(json.dumps({**recorded, 'commit': '0' * 40}))
        self.assertIn('not on official maker main', ci('Install the recorded maker tooling', ok=False))
        (self.app / 'maker.lock').write_text(json.dumps({**recorded, 'sha256': '0' * 64}))
        self.assertIn('does not match the sha256 in maker.lock', ci('Install the recorded maker tooling', ok=False))

    def test_long_running_start_does_not_hold_update_selection_lock(self):
        workspace = self.maker / 'scripts/workspace.py'
        workspace.write_text(workspace.read_text().replace('def main():', """def main():
    if sys.argv[1:] == ['start']:
        (ROOT / '.local/start-ready').touch()
        sys.stdin.read()
        return"""))
        self.commit(self.maker, 'Fixture launcher waits for input instead of starting a server')
        self.git(self.maker, 'push')
        self.command('update')
        process = subprocess.Popen([str(self.app / 'bilibi'), 'start'], cwd=self.root,
                                   env=self.env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not (self.app / '.local/start-ready').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.app / '.local/start-ready').exists())
            self.command('update')
            self.assertIsNone(process.poll())
        finally:
            process.stdin.close()
            process.wait(timeout=5)

    def test_interrupted_runtime_retains_selection_and_retries(self):
        workspace = self.maker / 'scripts/workspace.py'
        workspace.write_text(workspace.read_text().replace('def main():', """def main():
    marker = ROOT / '.local/interrupted-once'
    if not marker.exists():
        marker.touch()
        os.kill(os.getpid(), 15)"""))
        self.commit(self.maker, 'Interrupt once after maker installation')
        interrupted = self.git(self.maker, 'rev-parse', 'HEAD')
        self.git(self.maker, 'push')
        before = self.heads()
        result = subprocess.run([str(self.app / 'bilibi'), 'update'], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Application update failed', result.stderr)
        self.assertEqual(self.selected()['commit'], interrupted)
        self.assertEqual(before, self.heads())
        self.command('update')
        self.assertEqual(before, self.heads())
        self.assertEqual(self.selected()['commit'], interrupted)

    def test_trusted_upstream_mismatch_refused_before_execution(self):
        self.git(self.app, 'remote', 'set-url', 'upstream', 'https://github.com/BelimbingApp/bilibi-app-maker.git')
        before = self.snapshot()
        self.assertIn('upstream mismatch', self.command('update', ok=False))
        self.assertEqual(before, self.snapshot())
        self.git(self.app, 'remote', 'set-url', 'upstream', OFFICIAL)
        self.git(self.state / 'objects.git', 'remote', 'set-url', 'origin', 'https://example.test/untrusted.git')
        self.assertIn('trusted upstream mismatch', self.command('update', ok=False))

    def test_unsupported_maker_protocol_keeps_previous_selection(self):
        (self.maker / 'maker-tooling.toml').write_text('format = 2\n')
        self.commit(self.maker, 'Future unsupported format')
        self.git(self.maker, 'push')
        before = self.snapshot()
        marker = self.selected()
        self.assertIn('does not support this bootstrap', self.command('update', ok=False))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(marker, self.selected())


    def test_fresh_clone_runs_the_locked_maker_not_newer_main(self):
        self.maker_update()
        clone = self.root / 'teammate'
        self.git(self.root, 'clone', '-b', 'main', str(self.root / 'application.git'), str(clone))
        result = subprocess.run([str(clone / 'bilibi'), 'status'], env=self.env, cwd=self.root,
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(self.first, output)
        self.assertNotIn('NEW MAKER RUNTIME', output)
        self.assertEqual(json.loads((clone / 'maker.lock').read_text())['commit'], self.first)
        self.assertEqual(self.git(clone, 'status', '--porcelain'), '')
        self.assertFalse((clone / '.git/bilibi-maker' / self.second).exists())
        self.assertFalse((clone / '.git/bilibi-maker/selected.json').exists())

    def test_ordinary_commands_read_no_maker_history(self):
        # Neither fsck nor any other Git call on the cache: an installed folder
        # that still hashes to maker.lock's sha256 runs without it.
        (self.state / 'objects.git').rename(self.root / 'cache-set-aside')
        output = self.command('status')
        self.assertIn(self.first, output)
        self.assertFalse((self.state / 'objects.git').exists())

    def test_update_lists_maker_commits_before_switching_and_records_them(self):
        self.maker_update()
        output = self.command('update')
        short = self.second[:7]
        listing = output.index(f'{short} Official fixture tooling two')
        self.assertLess(output.index('Brings in 1 maker commit'), listing)
        compare = f'https://github.com/BelimbingProject/bilibi-app-maker/compare/{self.first}...{self.second}'
        self.assertLess(output.index(compare), output.index('NEW MAKER RUNTIME'))
        lock = self.selected()
        self.assertEqual(lock['commit'], self.second)
        self.assertEqual(lock['changes'], {'from': self.first, 'compare': compare,
                                           'adds': [f'{short} Official fixture tooling two'], 'removes': []})
        # The application pull request carries the list in its maker.lock diff.
        self.assertIn('Official fixture tooling two', self.git(self.app, 'diff', '--', 'maker.lock'))
        for name in ('maker.lock', 'maker.toml'):
            self.assertEqual((self.app / name).stat().st_mode & 0o777, 0o644, name)

    def test_rollback_returns_to_an_earlier_official_commit(self):
        self.maker_update()
        self.command('update')
        # Going back before committing simply returns to the committed record.
        self.assertIn(f'maker.lock records maker commit {self.first}', self.command('update', '--back'))
        self.assertEqual(self.selected()['commit'], self.first)
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), '')
        self.assertNotIn('NEW MAKER RUNTIME', self.command('status'))
        # A committed move goes back to the previous record in application history.
        self.command('update')
        self.git(self.app, 'commit', '-m', 'Take maker two', '--', 'maker.lock')
        self.git(self.app, 'push')
        self.assertIn('NEW MAKER RUNTIME', self.command('status'))
        output = self.command('update', '--back')
        self.assertIn('Removes 1 maker commit', output)
        self.assertIn(f'{self.second[:7]} Official fixture tooling two', output)
        self.assertNotIn('NEW MAKER RUNTIME', output)
        lock = self.selected()
        self.assertEqual(lock['commit'], self.first)
        self.assertEqual(lock['changes']['removes'], [f'{self.second[:7]} Official fixture tooling two'])
        self.assertEqual(lock['changes']['adds'], [])
        self.assertNotIn('NEW MAKER RUNTIME', self.command('status'))
        # --to names any official commit; the same checks apply.
        self.assertIn('NEW MAKER RUNTIME', self.command('update', '--to', self.second[:10]))
        self.assertEqual(self.selected()['commit'], self.second)
        self.git(self.maker, 'switch', '-c', 'unmerged')
        (self.maker / 'notes').write_text('not reviewed\n')
        self.commit(self.maker, 'Unmerged branch commit')
        unmerged = self.git(self.maker, 'rev-parse', 'HEAD')
        self.git(self.maker, 'push', 'origin', 'unmerged')
        self.git(self.state / 'objects.git', 'fetch', '--quiet', 'origin', 'unmerged')
        before = self.selected()
        self.assertIn('not on official maker main', self.command('update', '--to', unmerged, ok=False))
        self.assertIn('needs a maker commit ID', self.command('update', '--to', 'main', ok=False))
        self.assertIn('Use ./bilibi update', self.command('update', '--sideways', ok=False))
        self.assertEqual(self.selected(), before)

    def test_commit_hook_never_downloads_maker_code(self):
        tools = self.state / self.first / 'scripts'
        subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import workspace; workspace.hooks()',
                        str(tools)], env={**self.env, 'BILIBI_TOOL_APPLICATION_ROOT': str(self.app), 'PYTHONDONTWRITEBYTECODE': '1'},
                       cwd=self.root, check=True, capture_output=True, text=True, timeout=30)
        # Any network use from here on leaves a mark and fails.
        helper = self.root / 'git-core/git-remote-https'
        used = self.root / 'network-used'
        helper.write_text(f'#!/bin/sh\ntouch {used}\nexit 1\n')
        self.maker_update()
        # The locked tooling is installed: the hook runs it and still guards the record.
        (self.app / 'composition/Cargo.lock').write_text('# Tampered lock\n')
        self.git(self.app, 'add', 'composition/Cargo.lock')
        refused = subprocess.run(['git', '-C', str(self.app), 'commit', '-m', 'Inconsistent record'],
                                 env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.git(self.app, 'checkout', 'HEAD', '--', 'composition/Cargo.lock')
        # Without the locked tooling installed (a fresh clone), the hook does nothing.
        for folder in (self.state / self.first, self.state / 'objects.git'):
            shutil.rmtree(folder)
        (self.app / 'notes').write_text('hook test\n')
        self.git(self.app, 'add', 'notes')
        accepted = subprocess.run(['git', '-C', str(self.app), 'commit', '-m', 'Commit without tooling'],
                                  env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertIn('commit check skipped', accepted.stderr)
        self.assertFalse(used.exists())
        self.assertFalse((self.state / self.second).exists())
        self.assertFalse((self.state / 'objects.git').exists())


class AdoptionTest(WorkspaceFixture):
    def test_linked_older_official_copy_adopts_newer_runtime(self):
        remote = self.root / 'bilibi-app-maker.git'
        self.git(self.root, 'init', '--bare', str(remote))
        author = self.root / 'maker'
        self.git(self.root, 'clone', str(remote), str(author))
        self.git(author, 'checkout', '-b', 'main')
        shutil.copytree(ROOT / 'scripts', author / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        shutil.copy2(ROOT / 'maker-tooling.toml', author)
        self.commit(author, 'Official copied tooling ancestor')
        self.git(author, 'push', 'origin', 'main')
        self.git(self.app, 'fetch', str(remote), 'main')
        self.git(self.app, 'merge', '--allow-unrelated-histories', '-s', 'ours',
                 '-m', 'Application retains official copy ancestry', 'FETCH_HEAD')
        script = author / 'scripts/workspace.py'
        script.write_text(script.read_text().replace('def main():', "def main():\n    print('UPDATED OFFICIAL RUNTIME')"))
        self.commit(author, 'Newer official runtime')
        latest = self.git(author, 'rev-parse', 'HEAD')
        self.git(author, 'push', 'origin', 'main')
        self.assertIn('Adopted shared maker tooling', self.command('adopt'))
        self.assertIn(latest, self.command('status'))
        self.assertIn('UPDATED OFFICIAL RUNTIME', self.command('status'))
        self.assertEqual(self.git(self.app, 'remote', 'get-url', 'origin'), str(self.root / 'application.git'))

    def test_customized_legacy_tooling_is_preserved(self):
        maker = self.root / 'bilibi-app-maker.git'
        self.git(self.root, 'init', '--bare', str(maker))
        author = self.root / 'maker'
        self.git(self.root, 'clone', str(maker), str(author))
        self.git(author, 'checkout', '-b', 'main')
        shutil.copytree(ROOT / 'scripts', author / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        shutil.copy2(ROOT / 'maker-tooling.toml', author)
        self.commit(author, 'Tooling')
        self.git(author, 'push', 'origin', 'main')
        script = self.app / 'scripts/workspace.py'
        script.write_text(script.read_text() + '\n# valuable custom command\n')
        self.commit(self.app, 'Custom copied tooling')
        before = self.snapshot()
        self.assertIn('customized copied tooling', self.command('adopt', ok=False))
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.app / 'maker.toml').exists())
        self.assertTrue(script.read_text().endswith('# valuable custom command\n'))


if __name__ == '__main__':
    unittest.main()
