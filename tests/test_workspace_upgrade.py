"""Exercise ./bilibi upgrade with real Git, throwaway local remotes and a recording gh."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest

from test_workspace_build import BUILDER, RECORD
from test_workspace_update import DISCOVERY

ROOT = Path(__file__).resolve().parent.parent
MAKER_URL = 'https://github.com/BelimbingProject/bilibi-app-maker.git'
APP_URL = 'https://github.com/acme/shop.git'
PLATFORM_URL = 'https://github.com/BelimbingProject/bilibi.git'
FINANCE_URL = 'https://github.com/BelimbingProject/bilibi-dom-finance.git'


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
        # GitHub serves any commit by its ID; local upload-pack needs this to do the same.
        (self.root / 'gitconfig').write_text('[uploadpack]\n\tallowAnySHA1InWant = true\n')
        for name in ('bilibi-app-maker', 'shop', 'bilibi', 'bilibi-dom-finance'):
            self.git(self.root, 'init', '--bare', '-b', 'main', str(self.root / f'{name}.git'))
        self.platform_fixture()
        self.maker = self.root / 'maker-author'
        self.maker.mkdir()
        self.git(self.maker, 'init', '-b', 'main')
        shutil.copy2(ROOT / 'bilibi', self.maker)
        shutil.copy2(ROOT / '.gitignore', self.maker)
        shutil.copytree(ROOT / 'scripts', self.maker / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        # Pinning runs the real recipe materialize and restore; only cargo metadata and discovery --pin are replaced.
        (self.maker / 'scripts/builder.sh').write_text(BUILDER)
        (self.maker / 'scripts/test-record.py').write_text(RECORD)
        for name in ('builder.toml', 'rust-toolchain.toml'):
            (self.maker / name).write_text('# fixture\n')
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
        self.save_record(self.platform_first, self.finance_first)
        self.commit(self.app, 'Application one')
        self.git(self.app, 'push', '-u', 'origin', 'main')

    def platform_fixture(self):
        """An official Platform released as 0.1.0, and the finance Domain released for it, both in the catalogue."""
        self.platform_author = self.root / 'platform-author'
        self.git(self.root, 'clone', PLATFORM_URL, str(self.platform_author))
        (self.platform_author / 'scripts').mkdir()
        (self.platform_author / 'scripts/discover-modules.py').write_text(DISCOVERY)
        (self.platform_author / 'apps/domains/src').mkdir(parents=True)
        (self.platform_author / 'apps/extensions/src').mkdir(parents=True)
        (self.platform_author / 'apps/extensions/src/lib.rs').write_text('// empty\n')
        (self.platform_author / 'apps/domains/Cargo.toml').write_text('# Unmounted\n')
        (self.platform_author / 'Cargo.lock').write_text('# Platform lock\n')
        (self.platform_author / '.gitignore').write_text('/apps/domains/finance/\n/.local/\n__pycache__/\n')
        self.platform_first = self.platform_release('0.1.0', notes=None)
        self.finance_author = self.root / 'finance-author'
        self.git(self.root, 'clone', FINANCE_URL, str(self.finance_author))
        self.finance_first = self.finance_release('one')
        self.releases = {'0.1.0': self.platform_first}
        self.finance = {'0.1.0': (self.finance_first, '>=0.1.0, <0.2.0')}
        self.publish_catalogue()

    def platform_release(self, version, notes='# Upgrade notes for agents\n\n## Pull request 9: rename\n\nRename foo to bar.\n'):
        (self.platform_author / 'notes').write_text(f'platform {version}\n')
        if notes:
            (self.platform_author / 'docs/upgrade-notes').mkdir(parents=True, exist_ok=True)
            (self.platform_author / f'docs/upgrade-notes/v{version}.md').write_text(notes.replace('rename', f'rename in {version}'))
        self.commit(self.platform_author, f'Platform {version}')
        self.git(self.platform_author, 'push', 'origin', 'HEAD:main')
        return self.git(self.platform_author, 'rev-parse', 'HEAD')

    def finance_release(self, text):
        (self.finance_author / 'notes').write_text(f'finance {text}\n')
        self.commit(self.finance_author, f'Finance {text}')
        self.git(self.finance_author, 'push', 'origin', 'HEAD:main')
        return self.git(self.finance_author, 'rev-parse', 'HEAD')

    def publish_catalogue(self, finance_listed=True):
        """The Platform's published catalogue branch, as its Catalogue workflow writes it."""
        data = {'format': 1, 'plugins': {}, 'tested': [],
                'platform': {'name': 'Bilibi', 'repository': PLATFORM_URL.removesuffix('.git'),
                             'versions': {v: {'released': '2026-11-01', 'commit': c} for v, c in self.releases.items()}},
                'domains': {'finance': {'name': 'Finance', 'repository': FINANCE_URL.removesuffix('.git'), 'modules': [],
                                        'versions': {v: {'released': '2026-11-01', 'commit': c, 'platform': p}
                                                     for v, (c, p) in self.finance.items()}}} if finance_listed else {}}
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            self.git(folder, 'init', '-b', 'catalogue')
            (Path(folder) / 'catalogue.json').write_text(json.dumps(data, indent=1))
            self.commit(Path(folder), 'List releases')
            self.git(folder, 'push', '--force', PLATFORM_URL, 'catalogue')

    def save_record(self, platform, finance):
        (self.app / 'setup.toml').write_text(
            'format = 1\ntargets = ["x86_64-unknown-linux-gnu"]\n\n[platform]\n'
            f'url = "{PLATFORM_URL}"\ncommit = "{platform}"\n\n[[domains]]\nid = "finance"\n'
            f'url = "{FINANCE_URL}"\ncommit = "{finance}"\n')
        (self.app / 'composition').mkdir(exist_ok=True)
        lock = f'# Saved lock for {finance}\n'.encode()
        (self.app / 'composition/Cargo.lock').write_bytes(lock)
        (self.app / 'composition/manifest.txt').write_text(
            f'platform = "{platform}"\ncargo_lock_sha256 = "{hashlib.sha256(lock).hexdigest()}"\n'
            f'[containers."domains/finance"]\nhead = "{finance}"\n')

    def release_platform(self, version, finance_requirement):
        """Release the Platform and a finance release for it, and publish both in the catalogue."""
        self.releases[version] = self.platform_release(version)
        commit = self.finance_release(version)
        self.finance[version] = (commit, finance_requirement)
        self.publish_catalogue()
        return self.releases[version], commit

    def pinned(self, revision):
        return tomllib.loads(self.git(self.root / 'shop.git', 'show', f'{revision}:setup.toml'))

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
        (self.app / 'setup.toml').write_text((self.app / 'setup.toml').read_text() + '# our versions\n')
        self.commit(self.app, 'Our record')
        self.git(self.app, 'push')
        self.assertIn('checkout --ours -- setup.toml', self.command('upgrade', ok=False))

    def test_adopted_application_merges_no_maker_files(self):
        self.maker_change()
        (self.app / 'maker.lock').write_text('maker = "0"\n')
        self.commit(self.app, 'Adopt')
        self.git(self.app, 'push')
        before = self.snapshot()
        output = self.command('upgrade')
        for text in ('maker.lock with ./bilibi update', 'already at its newest release, v0.1.0', 'Nothing was changed'):
            self.assertIn(text, output)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.asked.exists())
        platform, finance = self.release_platform('0.2.0', '>=0.2.0, <0.3.0')
        self.command('upgrade')
        self.assertEqual(self.pinned('platform-upgrade-v0.2.0')['platform']['commit'], platform)
        self.assertNotIn('merge commit', self.asked.read_text())
        self.assertIn('Move the Platform to v0.2.0', self.asked.read_text())

    def test_platform_and_official_domain_move_in_the_same_pull_request_with_notes(self):
        tip = self.maker_change()
        ours = self.git(self.app, 'rev-parse', 'HEAD')
        platform, finance = self.release_platform('0.2.0', '>=0.2.0, <0.3.0')
        output = self.command('upgrade')
        for text in (f'Platform -> v0.2.0 ({platform[:12]})', f'finance -> 0.2.0 ({finance[:12]})',
                     '## Upgrade notes for agents', '### Platform v0.2.0', 'Pull request 9: rename in 0.2.0'):
            self.assertIn(text, output)
        branch = f'maker-upgrade-{tip[:7]}'
        pinned = self.pinned(branch)
        self.assertEqual((pinned['platform']['commit'], pinned['domains'][0]['commit']), (platform, finance))
        manifest = self.git(self.root / 'shop.git', 'show', f'{branch}:composition/manifest.txt')
        self.assertIn(f'platform = "{platform}"', manifest)
        self.assertIn(f'head = "{finance}"', manifest)
        # One branch: the pinned move on our history, then the maker merged into it.
        moved, maker = self.git(self.root / 'shop.git', 'rev-list', '--parents', '-1', branch).split()[1:]
        self.assertEqual((self.git(self.root / 'shop.git', 'rev-parse', f'{moved}^'), maker), (ours, tip))
        self.assertEqual(self.git(self.root / 'shop.git', 'log', '-1', '--format=%s', moved), 'Move the Platform to v0.2.0')
        asked = self.asked.read_text()
        for text in ('Merge maker updates', 'and move the Platform to v0.2.0', 'merge commit, not a squash',
                     '### Platform v0.2.0', '#### Pull request 9: rename in 0.2.0', 'Maker two (#7)'):
            self.assertIn(text, asked)
        self.assertNotIn('# Upgrade notes for agents\n\nBilibi', asked)
        self.assertEqual((self.git(self.app, 'branch', '--show-current'), self.git(self.app, 'rev-parse', 'HEAD')), ('main', ours))

    def test_platform_alone_moves_across_every_release_and_shows_each_notes(self):
        self.release_platform('0.2.0', '>=0.2.0, <0.4.0')
        platform, finance = self.release_platform('0.3.0', '>=0.3.0, <0.4.0')
        output = self.command('upgrade')
        self.assertIn('Already up to date with the maker', output)
        self.assertLess(output.index('### Platform v0.2.0'), output.index('### Platform v0.3.0'))
        self.assertNotIn('### Platform v0.1.0', output)
        self.assertEqual(self.pinned('platform-upgrade-v0.3.0')['platform']['commit'], platform)
        asked = self.asked.read_text()
        self.assertIn('Move the Platform to v0.3.0', asked)
        self.assertIn('rename in 0.2.0', asked)
        self.assertIn('rename in 0.3.0', asked)
        self.assertEqual(self.git(self.app, 'branch', '--show-current'), 'main')

    def test_platform_stays_when_an_official_domain_has_no_release_for_it(self):
        self.releases['0.2.0'] = self.platform_release('0.2.0')
        self.publish_catalogue()
        before = self.snapshot()
        output = self.command('upgrade')
        self.assertIn('finance has no release for Platform v0.2.0', output)
        self.assertIn('Nothing was changed', output)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.asked.exists())

    def test_component_outside_the_catalogue_keeps_its_commit(self):
        self.releases['0.2.0'] = self.platform_release('0.2.0')
        self.publish_catalogue(finance_listed=False)
        output = self.command('upgrade')
        self.assertIn('finance: not an official component, so it stays', output)
        self.assertEqual(self.pinned('platform-upgrade-v0.2.0')['domains'][0]['commit'], self.finance_first)

    def test_platform_never_moves_back(self):
        newer = self.platform_release('0.1.1-dev', notes=None)
        self.save_record(newer, self.finance_first)
        self.commit(self.app, 'Unreleased Platform')
        self.git(self.app, 'push')
        before = self.snapshot()
        self.assertIn('already past its newest release, v0.1.0', self.command('upgrade'))
        self.assertEqual(before, self.snapshot())

    def test_failed_pin_changes_nothing(self):
        (self.platform_author / 'scripts/discover-modules.py').write_text('raise SystemExit("broken discovery")\n')
        self.release_platform('0.2.0', '>=0.2.0, <0.3.0')
        before = self.snapshot()
        output = self.command('upgrade', ok=False)
        self.assertIn('broken discovery', output)
        self.assertIn('cannot pin the new Platform versions', output)
        self.assertIn('nothing was changed', output)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.asked.exists())

    def test_record_conflict_rerun_publishes_the_platform_move_and_its_notes(self):
        (self.maker / 'setup.toml').write_text('maker versions\n')
        self.commit(self.maker, 'Maker record')
        self.git(self.maker, 'push')
        platform, _ = self.release_platform('0.2.0', '>=0.2.0, <0.3.0')
        self.assertIn('checkout --ours -- setup.toml', self.command('upgrade', ok=False))
        self.git(self.app, 'checkout', '--ours', '--', 'setup.toml')
        self.git(self.app, 'add', '--all')
        self.git(self.app, 'commit', '--no-edit')
        self.command('upgrade')
        asked = self.asked.read_text()
        self.assertIn('Maker record', asked)
        self.assertIn('### Platform v0.2.0', asked)

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
        output = self.command('upgrade')
        for text in ('Already up to date with the maker', 'already at its newest release, v0.1.0', 'Nothing was changed'):
            self.assertIn(text, output)
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
