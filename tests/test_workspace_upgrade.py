"""Exercise ./bilibi upgrade with real Git, throwaway local remotes and a recording gh."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
MAKER_URL = 'https://github.com/BelimbingProject/bilibi-app-maker.git'
APP_URL = 'https://github.com/acme/shop.git'


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bilibi-upgrade-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key not in
                    ('BILIBI_SOURCE_DIR', 'BILIBI_BUILD_DIR', 'CARGO_TARGET_DIR')}
        # Real Git runs unchanged. Only its HTTPS transport helper is replaced, so the
        # real GitHub URLs are served from local bare repositories named after them.
        real_exec_path = Path(subprocess.check_output(['git', '--exec-path'], text=True).strip())
        exec_path = self.root / 'git-core'
        exec_path.mkdir()
        for entry in real_exec_path.iterdir():
            if not entry.name.startswith(('git-remote-http', 'git-remote-ftp')):
                (exec_path / entry.name).symlink_to(entry)
        helper = exec_path / 'git-remote-https'
        helper.write_text(f'#!/bin/sh\nexec git remote-ext "$1" "git %s {self.root}/$(basename "$2")"\n')
        helper.chmod(0o755)
        # gh only records what it was asked; set GH_FAILS to make it refuse.
        tools = self.root / 'bin'
        tools.mkdir()
        self.asked = self.root / 'gh-arguments'
        (tools / 'gh').write_text(f'#!/bin/sh\n[ -z "$GH_FAILS" ] || exit 1\n'
                                  f'printf \'%s\\n\' "$@" > {self.asked}\necho https://github.com/acme/shop/pull/1\n')
        (tools / 'gh').chmod(0o755)
        self.env.update(GIT_EXEC_PATH=str(exec_path), GIT_CONFIG_GLOBAL=str(self.root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test',
                        PATH=f'{tools}{os.pathsep}{self.env["PATH"]}')
        for name in ('bilibi-app-maker', 'shop'):
            self.git(self.root, 'init', '--bare', '-b', 'main', str(self.root / f'{name}.git'))
        self.maker = self.root / 'maker-author'
        self.maker.mkdir()
        self.git(self.maker, 'init', '-b', 'main')
        shutil.copy2(ROOT / 'bilibi', self.maker)
        shutil.copy2(ROOT / '.gitignore', self.maker)
        shutil.copytree(ROOT / 'scripts', self.maker / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        (self.maker / 'guide').write_text('maker guide one\n')
        self.commit(self.maker, 'Maker one')
        self.git(self.maker, 'remote', 'add', 'origin', MAKER_URL)
        self.git(self.maker, 'push', '-u', 'origin', 'main')
        # The owner's steps from "Start here": clone the maker, then connect their own repository.
        self.app = self.root / 'application'
        self.git(self.root, 'clone', MAKER_URL, str(self.app))
        self.git(self.app, 'remote', 'rename', 'origin', 'upstream')
        self.git(self.app, 'remote', 'add', 'origin', APP_URL)
        (self.app / 'product').write_text('our product\n')
        self.commit(self.app, 'Application one')
        self.git(self.app, 'push', '-u', 'origin', 'main')

    def git(self, folder, *args):
        return subprocess.check_output(['git', '-C', str(folder), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, folder, message):
        self.git(folder, 'add', '.')
        self.git(folder, 'commit', '-m', message)

    def command(self, *args, ok=True, **extra):
        result = subprocess.run([str(self.app / 'bilibi'), *args], env={**self.env, **extra},
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def maker_change(self, name='guide', text='maker guide two\n', message='Maker two (#7)'):
        (self.maker / name).write_text(text)
        self.commit(self.maker, message)
        self.git(self.maker, 'push')
        return self.git(self.maker, 'rev-parse', 'HEAD')

    def snapshot(self):
        return (self.git(self.app, 'rev-parse', 'HEAD'), self.git(self.app, 'branch', '--show-current'),
                self.git(self.app, 'branch', '--list'), self.git(self.app, 'status', '--porcelain'),
                self.git(self.root / 'shop.git', 'for-each-ref'))

    def refused(self, *expected, **extra):
        before = self.snapshot()
        output = self.command('upgrade', ok=False, **extra)
        for text in expected:
            self.assertIn(text, output)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.asked.exists())
        return output

    def test_clean_merge_pushes_a_branch_and_opens_one_pull_request(self):
        first = self.git(self.maker, 'rev-parse', 'HEAD')
        tip = self.maker_change()
        ours = self.git(self.app, 'rev-parse', 'HEAD')
        output = self.command('upgrade')
        self.assertIn('merge it with a merge commit, not a squash', output)
        branch = f'maker-upgrade-{tip[:7]}'
        # The branch on origin is a real merge: our history first, the maker's second.
        self.assertEqual(self.git(self.root / 'shop.git', 'rev-list', '--parents', '-1', branch).split()[1:], [ours, tip])
        self.assertEqual(self.git(self.root / 'shop.git', 'show', f'{branch}:guide'), 'maker guide two')
        self.assertEqual(self.git(self.root / 'shop.git', 'show', f'{branch}:product'), 'our product')
        # The workspace is back on an untouched default branch; nothing merged was run or kept there.
        self.assertEqual((self.git(self.app, 'branch', '--show-current'), self.git(self.app, 'rev-parse', 'HEAD')), ('main', ours))
        self.assertEqual(self.git(self.root / 'shop.git', 'rev-parse', 'main'), ours)
        asked = self.asked.read_text()
        for text in ('pr\ncreate\n--repo\nacme/shop\n--base\nmain\n--head\n' + branch,
                     f'{tip[:7]} Maker two (#7)', 'Maker commits (1)',
                     f'https://github.com/BelimbingProject/bilibi-app-maker/compare/{first}...{tip}',
                     'Merge this pull request with a merge commit, not a squash.'):
            self.assertIn(text, asked)
        self.assertNotIn('Maker one', asked)

    def test_conflict_stops_with_paths_and_steps_then_finishes_on_rerun(self):
        tip = self.maker_change()
        (self.app / 'guide').write_text('our guide\n')
        self.commit(self.app, 'Our guide')
        self.git(self.app, 'push')
        published = self.git(self.root / 'shop.git', 'for-each-ref')
        output = self.command('upgrade', ok=False)
        branch = f'maker-upgrade-{tip[:7]}'
        for text in ('Conflicted files:\n  guide\n', 'merge --abort', f'branch -D {branch}', 'commit --no-edit',
                     './bilibi upgrade again', 'nothing was pushed'):
            self.assertIn(text, output)
        self.assertEqual(self.git(self.app, 'branch', '--show-current'), branch)
        self.assertEqual(published, self.git(self.root / 'shop.git', 'for-each-ref'))
        self.assertFalse(self.asked.exists())
        # An unfinished merge is uncommitted work: a rerun still publishes nothing.
        self.assertIn('uncommitted work', self.command('upgrade', ok=False))
        self.assertFalse(self.asked.exists())
        (self.app / 'guide').write_text('our guide with maker guide two\n')
        self.git(self.app, 'add', '--all')
        self.git(self.app, 'commit', '--no-edit')
        self.command('upgrade')
        self.assertEqual(self.git(self.root / 'shop.git', 'show', f'{branch}:guide'), 'our guide with maker guide two')
        self.assertIn('Maker two (#7)', self.asked.read_text())
        self.assertEqual(self.git(self.app, 'branch', '--show-current'), 'main')

    def test_record_conflict_says_to_keep_the_application_versions(self):
        (self.maker / 'setup.toml').write_text('maker versions\n')
        self.commit(self.maker, 'Maker record')
        self.git(self.maker, 'push')
        (self.app / 'setup.toml').write_text('our versions\n')
        self.commit(self.app, 'Our record')
        self.git(self.app, 'push')
        self.assertIn('checkout --ours -- setup.toml', self.command('upgrade', ok=False))

    def test_dirty_checkout_is_refused_before_anything_is_fetched(self):
        self.maker_change()
        for name, text in (('product', 'valuable edit\n'), ('untracked', 'keep this\n')):
            with self.subTest(name=name):
                saved = (self.app / name).read_bytes() if (self.app / name).exists() else None
                (self.app / name).write_text(text)
                output = self.refused('uncommitted work', 'rerun ./bilibi upgrade')
                self.assertIn(name, output)
                self.assertEqual((self.app / name).read_text(), text)
                self.assertEqual(self.git(self.app, 'rev-parse', 'upstream/main'), self.git(self.app, 'rev-parse', 'HEAD^'))
                (self.app / name).write_bytes(saved) if saved is not None else (self.app / name).unlink()

    def test_wrong_upstream_is_refused(self):
        self.maker_change()
        self.git(self.root, 'clone', '--bare', str(self.root / 'bilibi-app-maker.git'), str(self.root / 'fork.git'))
        for url in ('https://github.com/BelimbingApp/bilibi-app-maker.git', 'https://github.com/someone/fork.git',
                    'https://github.com/BelimbingProject/bilibi-app-maker', str(self.root / 'bilibi-app-maker.git')):
            with self.subTest(url=url):
                self.git(self.app, 'remote', 'set-url', 'upstream', url)
                self.refused(f'upstream remote must be exactly {MAKER_URL}', f'remote set-url upstream {MAKER_URL}')
        self.git(self.app, 'remote', 'remove', 'upstream')
        self.refused(f'remote add upstream {MAKER_URL}')

    def test_url_rewrite_cannot_pass_as_the_maker(self):
        self.maker_change()
        self.git(self.app, 'config', 'url.https://github.com/someone/fork.git.insteadOf', MAKER_URL)
        self.refused('upstream remote must be exactly')

    def test_already_up_to_date_changes_nothing(self):
        before = self.snapshot()
        self.assertIn('Already up to date with the maker', self.command('upgrade'))
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.asked.exists())

    def test_starts_only_from_a_default_branch_level_with_origin(self):
        self.maker_change()
        self.git(self.app, 'commit', '--allow-empty', '-m', 'Unpushed')
        self.refused('up-to-date main branch', './bilibi update')
        self.git(self.app, 'reset', '--hard', 'HEAD^')
        self.git(self.app, 'switch', '--create', 'feature')
        self.refused('up-to-date main branch')
        self.git(self.app, 'switch', '--detach')
        self.refused('up-to-date main branch')

    def test_origin_must_be_the_owners_github_repository(self):
        self.maker_change()
        for url in (MAKER_URL, str(self.root / 'shop.git')):
            with self.subTest(url=url):
                self.git(self.app, 'remote', 'set-url', 'origin', url)
                self.refused('origin must be your own application repository')

    def test_unlinked_history_points_to_the_guide(self):
        self.maker_change()
        self.git(self.app, 'checkout', '--orphan', 'fresh')
        self.commit(self.app, 'Copied from a ZIP')
        self.git(self.app, 'branch', '-M', 'main')
        self.git(self.app, 'push', '--force', 'origin', 'main')
        self.refused('not linked to the maker', 'docs/guides/maker-updates.md')

    def test_failed_pull_request_keeps_the_pushed_branch_and_says_what_to_open(self):
        tip = self.maker_change()
        output = self.command('upgrade', ok=False, GH_FAILS='1')
        branch = f'maker-upgrade-{tip[:7]}'
        self.assertIn(f'https://github.com/acme/shop/compare/main...{branch}?expand=1', output)
        self.assertIn('merge commit, not a squash', output)
        self.assertEqual(self.git(self.root / 'shop.git', 'rev-parse', f'{branch}^2'), tip)
        self.assertEqual(self.git(self.app, 'branch', '--show-current'), 'main')

    def test_help_lists_upgrade(self):
        self.assertIn('  upgrade ', self.command('help'))


if __name__ == '__main__':
    unittest.main()
