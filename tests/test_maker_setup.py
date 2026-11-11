"""Run standalone first setup against local Git history; never install host tools."""
import json
import shutil
import subprocess

from test_maker_update import fake_prerequisites
from test_workspace_update import ROOT, WorkspaceFixture


class SetupTest(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.maker = self.root / 'maker'
        self.remote = self.root / 'bilibi-app-maker.git'
        self.git(self.root, 'init', '--bare', str(self.remote))
        self.git(self.root, 'clone', str(self.remote), str(self.maker))
        self.git(self.maker, 'checkout', '-b', 'main')
        shutil.copytree(ROOT / 'scripts', self.maker / 'scripts',
                        ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        shutil.copy2(ROOT / 'maker-tooling.toml', self.maker)
        self.commit(self.maker, 'Official copied tooling ancestor')
        self.ancestor = self.git(self.maker, 'rev-parse', 'HEAD')
        self.git(self.maker, 'push', 'origin', 'main')
        self.git(self.app, 'fetch', str(self.remote), 'main')
        self.git(self.app, 'merge', '--allow-unrelated-histories', '-s', 'ours',
                 '-m', 'Application keeps old maker ancestry', 'FETCH_HEAD')
        fake_prerequisites(self.maker)
        self.publish('New official runtime with disposable prerequisite seam')

    def publish(self, message):
        self.commit(self.maker, message)
        self.latest = self.git(self.maker, 'rev-parse', 'HEAD')
        self.git(self.maker, 'push', 'origin', 'main')

    def tracked_bytes(self):
        return {name: ((self.app / name).read_bytes(), (self.app / name).stat().st_mode)
                for name in self.git(self.app, 'ls-files').splitlines()}

    def selected(self):
        return json.loads((self.app / 'maker.lock').read_text())['commit']

    def assert_not_prepared(self):
        self.assertFalse((self.app / '.local/prerequisites').exists())
        self.assertFalse((self.app / 'maker.toml').exists())
        self.assertFalse((self.app / 'maker.lock').exists())

    def test_unchanged_older_copy_transitions_before_prerequisites_and_preserves_app(self):
        for name in ('README.md', 'builder.toml', 'rust-toolchain.toml', '.env'):
            (self.app / name).write_text('Application owned ' + name + '\n')
        self.commit(self.app, 'Application settings')
        # The reviewed replacement launcher may be the one uncommitted file.
        (self.app / 'bilibi').write_text((self.app / 'bilibi').read_text() + '\n# reviewed launcher\n')
        before = self.tracked_bytes()
        output = self.command('setup')
        self.assertLess(output.index('created maker.toml and maker.lock'), output.index('FIXTURE_PREREQUISITES'))
        self.assertLess(output.index('FIXTURE_PREREQUISITES'), output.index('FIXTURE_BUILDER_SETUP'))
        self.assertEqual(self.tracked_bytes(), before)
        self.assertEqual(self.selected(), self.latest)
        self.assertIn('Setup complete', output)
        self.assertIn('only ./bilibi update selects a newer maker', output)
        self.assertEqual((self.app / '.local/prerequisites').read_text(), 'prepared\n')

    def test_later_official_copies_with_old_ancestor_and_app_ci_helper(self):
        # B was copied, not merged; C is newer still. A merge-base-only guard
        # mistakes both a B copy and an exact C copy for application changes.
        script = self.maker / 'scripts/workspace.py'
        script.write_text(script.read_text() + '\n# official version B\n')
        obsolete = self.maker / 'scripts/obsolete-builder.sh'
        obsolete.write_text('# official retired builder\n')
        self.publish('Official version B')
        shutil.copy2(script, self.app / 'scripts/workspace.py')
        obsolete.unlink()
        script.write_text(script.read_text() + '\n# official version C\n')
        (self.maker / 'scripts/check.sh').write_text('#!/bin/sh\n# official version C\n')
        self.publish('Official version C retires a builder')
        shutil.copy2(self.maker / 'scripts/check.sh', self.app / 'scripts/check.sh')
        helper = self.app / 'scripts/app-ci-cleanup.sh'
        helper.write_text('#!/bin/sh\necho application cleanup\n')
        workflow = self.app / '.github/workflows/app.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_text('steps:\n  - run: bash scripts/app-ci-cleanup.sh\n')
        self.commit(self.app, 'Copy official files and retain application CI')
        self.git(self.app, 'fetch', str(self.remote), 'main')
        self.assertEqual(self.git(self.app, 'merge-base', 'HEAD', self.latest), self.ancestor)
        before = self.tracked_bytes()
        output = self.command('setup')
        self.assertIn('Retained application helper: scripts/app-ci-cleanup.sh', output)
        self.assertEqual(before, self.tracked_bytes())
        self.assertEqual(self.selected(), self.latest)
        self.assertIn('scripts/app-ci-cleanup.sh', workflow.read_text())

    def test_official_deletion_since_old_ancestor_is_accepted(self):
        obsolete = self.maker / 'scripts/retired-builder.sh'
        obsolete.write_text('# official old builder\n')
        self.publish('Old official builder')
        self.git(self.app, 'fetch', str(self.remote), 'main')
        self.git(self.app, 'merge', '-s', 'ours', '-m', 'Old builder ancestry', 'FETCH_HEAD')
        obsolete.unlink()
        self.publish('Officially retire builder')
        self.assertIn('Setup complete', self.command('setup'))
        self.assertFalse((self.app / 'scripts/retired-builder.sh').exists())

    def test_custom_managed_content_mode_or_symlink_stops_before_setup(self):
        script = self.app / 'scripts/workspace.py'
        original = script.read_bytes()
        original_mode = script.stat().st_mode
        for kind in ('content', 'mode', 'symlink'):
            with self.subTest(kind=kind):
                if kind == 'content':
                    script.write_bytes(original + b'\n# valuable application behavior\n')
                elif kind == 'mode':
                    script.chmod(0o755)
                else:
                    script.unlink()
                    script.symlink_to('recipe.py')
                self.commit(self.app, 'Application managed customization ' + kind)
                before = self.snapshot()
                output = self.command('setup', ok=False)
                self.assertIn('scripts/workspace.py: customized copied tooling', output)
                self.assertIn('git log -p -- scripts/workspace.py', output)
                self.assertEqual(self.snapshot(), before)
                self.assert_not_prepared()
                if script.is_symlink():
                    script.unlink()
                script.write_bytes(original)
                script.chmod(original_mode)
                self.commit(self.app, 'Restore official copy')

    def test_dirty_app_refusal_and_network_failure_do_not_prepare(self):
        note = self.app / 'untracked-work'
        note.write_text('precious\n')
        self.assertIn('First setup requires a clean application', self.command('setup', ok=False))
        self.assertEqual(note.read_text(), 'precious\n')
        self.assert_not_prepared()
        note.unlink()
        (self.root / 'git-core/git-remote-https').write_text('#!/bin/sh\nexit 1\n')
        self.assertIn('Cannot obtain official maker main', self.command('setup', ok=False))
        self.assert_not_prepared()

    def test_application_deletion_of_managed_behavior_requires_a_port(self):
        script = self.app / 'scripts/check.sh'
        script.unlink()
        self.commit(self.app, 'Application disables managed check')
        output = self.command('setup', ok=False)
        self.assertIn('scripts/check.sh: deleted copied tooling', output)
        self.assert_not_prepared()
        self.assertFalse(script.exists())

    def test_prerequisite_failure_keeps_pin_and_retry_does_not_repin(self):
        (self.app / '.local/fail-prerequisites').touch()
        selected = self.latest
        output = self.command('setup', ok=False)
        self.assertIn('First setup adopted', output)
        self.assertNotIn('FIXTURE_BUILDER_SETUP', output)
        self.assertEqual(self.selected(), selected)
        (self.app / '.local/fail-prerequisites').unlink()
        script = self.maker / 'scripts/workspace.py'
        script.write_text(script.read_text() + '\n# later official version\n')
        self.publish('Newer maker must not be selected by retry')
        output = self.command('setup')
        self.assertNotIn('created maker.toml', output)
        self.assertEqual(self.selected(), selected)
        self.assertEqual((self.app / '.local/prerequisites').read_text(), 'prepared\nprepared\n')

    def test_locked_rerun_offline_and_fresh_clone_keep_committed_pin(self):
        self.command('setup')
        self.commit(self.app, 'Record shared tooling')
        pinned = self.latest
        lock = (self.app / 'maker.lock').read_bytes()
        script = self.maker / 'scripts/workspace.py'
        script.write_text(script.read_text() + '\n# next maker\n')
        self.publish('Maker advances')
        clone = self.root / 'fresh'
        self.git(self.root, 'clone', '-b', 'main', str(self.app), str(clone))
        output = subprocess.run([str(clone / 'bilibi'), 'setup'], env=self.env, cwd=self.root,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
        self.assertEqual((clone / 'maker.lock').read_bytes(), lock)
        self.assertEqual(self.git(clone, 'status', '--porcelain'), '')
        self.assertFalse((clone / '.git/bilibi-maker' / self.latest).exists())
        # The lock alone must never fall back to legacy dispatch or repin.
        (self.app / 'maker.toml').unlink()
        self.commit(self.app, 'Keep only the exact maker lock')
        helper = self.root / 'git-core/git-remote-https'
        helper.write_text('#!/bin/sh\nexit 1\n')
        self.command('setup')
        self.assertEqual(self.selected(), pinned)
        self.assertEqual((self.app / 'maker.lock').read_bytes(), lock)
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), '')

    def test_missing_lock_is_a_failure_not_implicit_repin(self):
        self.command('setup')
        self.commit(self.app, 'Record shared tooling')
        (self.app / 'maker.lock').unlink()
        self.assertIn('maker.lock is missing; restore it', self.command('setup', ok=False))
        self.assertEqual((self.app / '.local/prerequisites').read_text(), 'prepared\n')
