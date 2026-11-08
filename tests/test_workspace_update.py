"""Exercise the public wrapper against disposable application and source remotes."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PLATFORM_URL = 'https://example.test/platform.git'
DOMAIN_URL = 'https://example.test/finance.git'

# A small Platform fixture with the same discovery/restore interface. No Cargo,
# Tool installation, network access or production source checkout is needed.
DISCOVERY = '''from pathlib import Path
import shutil
import sys
ROOT = Path(__file__).resolve().parent.parent
def discovered(root):
    return (root / 'apps/domains/finance/notes').read_text().strip()
def outputs(root, modules):
    return {root / 'apps/domains/Cargo.toml': '# Generated for ' + modules + '\\n'}
def version_at_head(label, folder):
    return None
if __name__ == '__main__':
    files = outputs(ROOT, discovered(ROOT))
    if '--restore' in sys.argv:
        for path, text in files.items():
            path.write_text(text)
        shutil.copyfile(ROOT / '.local/composition/Cargo.lock', ROOT / 'Cargo.lock')
    elif '--check' in sys.argv:
        assert all(path.read_text() == text for path, text in files.items())
'''


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bilibi-update-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key not in
                    ('BILIBI_SOURCE_DIR', 'BILIBI_BUILD_DIR', 'CARGO_TARGET_DIR')}
        # Real Git runs unchanged. Only its HTTPS transport helper is replaced so the
        # fixture HTTPS origins are served from local bare repositories.
        real_exec_path = Path(subprocess.check_output(['git', '--exec-path'], text=True).strip())
        exec_path = self.root / 'git-core'
        exec_path.mkdir()
        for entry in real_exec_path.iterdir():
            if not entry.name.startswith(('git-remote-http', 'git-remote-ftp')):
                (exec_path / entry.name).symlink_to(entry)
        helper = exec_path / 'git-remote-https'
        helper.write_text(f'#!/bin/sh\nexec git remote-ext "$1" "git %s {self.root}/$(basename "$2")"\n')
        helper.chmod(0o755)
        self.env.update(GIT_EXEC_PATH=str(exec_path), GIT_CONFIG_GLOBAL=str(self.root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test')
        for name in ('platform', 'finance'):
            self.git(self.root, 'init', '--bare', str(self.root / f'{name}.git'))
        self.domain_origin = self.root / 'finance-author'
        self.git(self.root, 'clone', DOMAIN_URL, str(self.domain_origin))
        (self.domain_origin / 'notes').write_text('first domain\n')
        self.commit(self.domain_origin, 'Domain one')
        self.domain_first = self.git(self.domain_origin, 'rev-parse', 'HEAD')
        self.git(self.domain_origin, 'push', 'origin', 'HEAD')
        self.platform_origin = self.root / 'platform-author'
        self.git(self.root, 'clone', PLATFORM_URL, str(self.platform_origin))
        (self.platform_origin / 'scripts').mkdir()
        (self.platform_origin / 'scripts/discover-modules.py').write_text(DISCOVERY)
        (self.platform_origin / 'apps/domains/src').mkdir(parents=True)
        (self.platform_origin / 'apps/extensions/src').mkdir(parents=True)
        (self.platform_origin / 'apps/extensions/src/lib.rs').write_text('// empty\n')
        (self.platform_origin / 'apps/domains/Cargo.toml').write_text('# Unmounted\n')
        (self.platform_origin / 'Cargo.lock').write_text('# Platform lock\n')
        (self.platform_origin / '.gitignore').write_text('/apps/domains/finance/\n/apps/extensions/extra/\n/.local/\n__pycache__/\n')
        (self.platform_origin / 'notes').write_text('platform work\n')
        self.commit(self.platform_origin, 'Platform one')
        self.platform_first = self.git(self.platform_origin, 'rev-parse', 'HEAD')
        self.git(self.platform_origin, 'push', 'origin', 'HEAD')
        self.author = self.root / 'app-author'
        self.author.mkdir()
        self.git(self.author, 'init', '-b', 'main')
        shutil.copy2(ROOT / 'bilibi', self.author)
        shutil.copytree(ROOT / 'scripts', self.author / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        (self.author / '.gitignore').write_text('/sources/\n/.local/\n/build/\n__pycache__/\n')
        (self.author / 'notes').write_text('first application\n')
        self.save_recipe(self.platform_first, self.domain_first)
        self.commit(self.author, 'Application one')
        self.git(self.root, 'init', '--bare', str(self.root / 'application.git'))
        self.git(self.author, 'remote', 'add', 'origin', str(self.root / 'application.git'))
        self.git(self.author, 'push', '-u', 'origin', 'main')
        self.app = self.root / 'application'
        self.git(self.root, 'clone', '-b', 'main', str(self.root / 'application.git'), str(self.app))
        self.platform = self.app / 'sources/platform'
        self.command('update')
        self.domain = self.platform / 'apps/domains/finance'

    def git(self, folder, *args):
        return subprocess.check_output(['git', '-C', str(folder), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, folder, message):
        self.git(folder, 'add', '.')
        self.git(folder, 'commit', '-m', message)

    def command(self, *args, ok=True):
        result = subprocess.run([str(self.app / 'bilibi'), *args], env=self.env,
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def save_recipe(self, platform, domain):
        (self.author / 'setup.toml').write_text(
            'format = 1\ntargets = ["x86_64-unknown-linux-gnu"]\n[platform]\n'
            f'url = "{PLATFORM_URL}"\ncommit = "{platform}"\n[[domains]]\nid = "finance"\n'
            f'url = "{DOMAIN_URL}"\ncommit = "{domain}"\n')
        composition = self.author / 'composition'
        composition.mkdir(exist_ok=True)
        lock = f'# Saved lock for {domain}\n'.encode()
        (composition / 'Cargo.lock').write_bytes(lock)
        (composition / 'manifest.txt').write_text(
            f'platform = "{platform}"\ncargo_lock_sha256 = "{hashlib.sha256(lock).hexdigest()}"\n'
            f'[containers."domains/finance"]\nhead = "{domain}"\n')

    def upstream(self, components=True):
        if components:
            (self.domain_origin / 'notes').write_text('second domain\n')
            self.commit(self.domain_origin, 'Domain two')
            self.domain_second = self.git(self.domain_origin, 'rev-parse', 'HEAD')
            self.git(self.domain_origin, 'push', 'origin', 'HEAD')
            (self.platform_origin / 'notes').write_text('second platform\n')
            self.commit(self.platform_origin, 'Platform two')
            self.platform_second = self.git(self.platform_origin, 'rev-parse', 'HEAD')
            self.git(self.platform_origin, 'push', 'origin', 'HEAD')
            self.save_recipe(self.platform_second, self.domain_second)
        (self.author / 'notes').write_text('upstream application\n')
        self.commit(self.author, 'Application two')
        self.git(self.author, 'push')

    def snapshot(self):
        state = []
        for folder in (self.app, self.platform, self.domain):
            state.append((self.git(folder, 'rev-parse', 'HEAD'),
                          self.git(folder, 'symbolic-ref', '-q', 'HEAD') if
                          self.git(folder, 'branch', '--show-current') else '',
                          self.git(folder, 'status', '--porcelain'),
                          self.git(folder, 'diff', '--binary'), self.git(folder, 'diff', '--cached', '--binary')))
        return state



class UpdateTest(WorkspaceFixture):
    def test_update_pulls_recipe_restores_sources_and_regenerates(self):
        self.upstream()
        expected = {name: (self.author / name).read_bytes() for name in
                    ('setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock')}
        self.assertIn('Updated application', self.command('update'))
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), self.git(self.author, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.platform, 'rev-parse', 'HEAD'), self.platform_second)
        self.assertEqual(self.git(self.domain, 'rev-parse', 'HEAD'), self.domain_second)
        self.assertEqual((self.platform / 'apps/domains/Cargo.toml').read_text(), '# Generated for second domain\n')
        self.assertEqual((self.platform / 'Cargo.lock').read_bytes(), expected['composition/Cargo.lock'])
        for name, contents in expected.items():
            self.assertEqual((self.app / name).read_bytes(), contents)
        before = self.snapshot()
        self.command('update')
        self.assertEqual(before, self.snapshot())

    def test_refuses_edits_without_changing_any_checkout(self):
        self.upstream()
        for folder, path in ((self.app, 'notes'), (self.platform, 'notes'), (self.domain, 'notes'),
                             (self.platform, 'apps/domains/Cargo.toml'), (self.platform, 'Cargo.lock')):
            with self.subTest(folder=folder, path=path):
                target = folder / path
                saved = target.read_bytes()
                target.write_bytes(saved + b'valuable edit\n')
                before = self.snapshot()
                output = self.command('update', ok=False)
                self.assertIn(str(folder), output)
                self.assertIn('commit or stash it yourself', output)
                self.assertIn('git -C', output)
                self.assertEqual(before, self.snapshot())
                self.assertEqual(target.read_bytes(), saved + b'valuable edit\n')
                target.write_bytes(saved)

    def test_refuses_unpushed_commits_without_changing_any_checkout(self):
        self.upstream()
        for folder in (self.app, self.platform, self.domain):
            with self.subTest(folder=folder):
                self.git(folder, 'checkout', '-b', 'private-work')
                if folder == self.app:
                    self.git(folder, 'branch', '--set-upstream-to', 'origin/main')
                self.git(folder, 'commit', '--allow-empty', '-m', 'Unpushed')
                before = self.snapshot()
                output = self.command('update', ok=False)
                self.assertIn(str(folder), output)
                self.assertIn('publish with git -C', output)
                self.assertEqual(before, self.snapshot())
                # Fixture cleanup switches back; update itself must never do so.
                if folder == self.app:
                    self.git(folder, 'checkout', 'main')
                else:
                    original = self.platform_first if folder == self.platform else self.domain_first
                    self.git(folder, 'checkout', '--detach', original)

    def test_record_published_only_as_a_tag_is_not_called_unpushed(self):
        self.git(self.domain_origin, 'checkout', '-b', 'release-work')
        (self.domain_origin / 'notes').write_text('tagged release\n')
        self.commit(self.domain_origin, 'Tagged release')
        tagged = self.git(self.domain_origin, 'rev-parse', 'HEAD')
        self.git(self.domain_origin, 'push', 'origin', 'HEAD:refs/tags/release-1')
        self.git(self.domain_origin, 'checkout', '-')
        self.git(self.domain_origin, 'branch', '-D', 'release-work')
        self.save_recipe(self.platform_first, tagged)
        self.commit(self.author, 'Record tagged release')
        self.git(self.author, 'push')
        self.command('update')
        self.assertEqual(self.git(self.domain, 'rev-parse', 'HEAD'), tagged)
        self.upstream()
        self.command('update')
        self.assertEqual(self.git(self.domain, 'rev-parse', 'HEAD'), self.domain_second)

    def test_refuses_staged_and_untracked_work(self):
        self.upstream()
        staged = self.platform / 'apps/domains/Cargo.toml'
        self.git(self.platform, 'add', str(staged))
        before = self.snapshot()
        self.assertIn('uncommitted work', self.command('update', ok=False))
        self.assertEqual(before, self.snapshot())
        self.git(self.platform, 'restore', '--staged', str(staged))
        unknown = self.domain / 'untracked'
        unknown.write_text('keep this\n')
        before = self.snapshot()
        self.assertIn(str(self.domain), self.command('update', ok=False))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(unknown.read_text(), 'keep this\n')

    def test_published_source_branch_is_preserved_when_returning_to_saved_record(self):
        self.git(self.domain, 'checkout', '-b', 'published-work')
        self.git(self.domain, 'commit', '--allow-empty', '-m', 'Published work')
        published = self.git(self.domain, 'rev-parse', 'HEAD')
        self.git(self.domain, 'push', 'origin', 'published-work')
        self.command('update')
        self.assertEqual(self.git(self.domain, 'rev-parse', 'HEAD'), self.domain_first)
        self.assertEqual(self.git(self.domain, 'rev-parse', 'published-work'), published)

    def test_help_and_no_command_are_plain_and_do_not_need_a_recipe(self):
        (self.app / 'setup.toml').unlink()
        output = self.command('help')
        self.assertTrue(output.startswith('This is a Bilibi application: your own product, built on Bilibi.\n'))
        self.assertIn('Usage: ./bilibi <command>\n', output)
        self.assertIn('  update ', output)
        self.assertEqual(output, self.command())
        self.assertIn('usage: ./bilibi', self.command('-h'))

    def test_no_upstream_and_detached_application_have_recovery_steps(self):
        self.git(self.app, 'branch', '--unset-upstream')
        output = self.command('update', ok=False)
        self.assertIn(str(self.app), output)
        self.assertIn('--set-upstream origin main', output)
        self.git(self.app, 'checkout', '--detach')
        self.assertIn('switch', self.command('update', ok=False))

    def test_origin_mismatch_and_missing_saved_commit_do_not_move_checkouts(self):
        self.upstream()
        self.git(self.domain, 'remote', 'set-url', 'origin', PLATFORM_URL)
        before = self.snapshot()
        output = self.command('update', ok=False)
        self.assertIn(str(self.domain), output)
        self.assertIn(f'remote set-url origin {DOMAIN_URL}', output)
        self.assertEqual(before, self.snapshot())
        self.git(self.domain, 'remote', 'set-url', 'origin', DOMAIN_URL)
        missing = '0' * 40
        self.save_recipe(self.platform_second, missing)
        self.commit(self.author, 'Unavailable source commit')
        self.git(self.author, 'push')
        before = self.snapshot()
        output = self.command('update', ok=False)
        self.assertIn(str(self.domain), output)
        self.assertIn(f'fetch origin {missing}', output)
        self.assertEqual(before, self.snapshot())

    def test_unlisted_clone_and_symlink_are_preserved(self):
        self.upstream()
        extra = self.platform / 'apps/extensions/extra'
        self.git(self.platform, 'clone', DOMAIN_URL, str(extra))
        with (self.platform / '.git/info/exclude').open('a') as stream:
            stream.write('/apps/extensions/extra/\n')
        before = self.snapshot()
        self.assertIn(str(extra), self.command('update', ok=False))
        self.assertEqual(before, self.snapshot())
        kept = self.root / 'kept-extra'
        extra.rename(kept)
        extra.symlink_to(kept, target_is_directory=True)
        self.assertIn('conflicting source path', self.command('update', ok=False))
        self.assertTrue(extra.is_symlink())
        self.assertEqual((kept / 'notes').read_text(), 'second domain\n')


if __name__ == '__main__':
    unittest.main()
