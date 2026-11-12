"""Exercise ./bilibi own with real Git, throwaway local remotes and a gh that serves them."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
MAKER_URL = 'https://github.com/BelimbingProject/bilibi-app-maker.git'
APP_URL = 'https://github.com/acme/shop.git'

# gh answers from the same local bare repositories that Git pushes to: "repo view"
# reports whether one exists and is empty, "repo create" makes it. Every call is logged.
GH = '''#!/bin/sh
printf '%s\\n' "$*" >> {log}
[ -z "$GH_FAILS" ] || {{ echo 'HTTP 401: Bad credentials' >&2; exit 1; }}
bare={root}/$(basename "$3").git
case "$1 $2" in
  "repo view")
    [ -d "$bare" ] || {{ echo "GraphQL: Could not resolve to a Repository with the name '$3'." >&2; exit 1; }}
    if [ -z "$(git -C "$bare" for-each-ref)" ]; then empty=true; else empty=false; fi
    echo "{{\\"isEmpty\\":$empty,\\"visibility\\":\\"PRIVATE\\"}}" ;;
  "repo create") git init --quiet --bare -b main "$bare" ;;
esac
'''


class OwnTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bilibi-own-test-')
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
        tools = self.root / 'bin'
        tools.mkdir()
        self.asked = self.root / 'gh-calls'
        (tools / 'gh').write_text(GH.format(log=self.asked, root=self.root))
        (tools / 'gh').chmod(0o755)
        self.env.update(GIT_EXEC_PATH=str(exec_path), GIT_CONFIG_GLOBAL=str(self.root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test',
                        PATH=f'{tools}{os.pathsep}{self.env["PATH"]}')
        self.git(self.root, 'init', '--bare', '-b', 'main', str(self.root / 'bilibi-app-maker.git'))
        maker = self.root / 'maker-author'
        maker.mkdir()
        self.git(maker, 'init', '-b', 'main')
        shutil.copy2(ROOT / 'bilibi', maker)
        shutil.copy2(ROOT / '.gitignore', maker)
        shutil.copytree(ROOT / 'scripts', maker / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        self.git(maker, 'add', '.')
        self.git(maker, 'commit', '-m', 'Maker one')
        self.git(maker, 'remote', 'add', 'origin', MAKER_URL)
        self.git(maker, 'push', '-u', 'origin', 'main')
        # Cloned as "Start here" shows: the address has no .git suffix.
        self.app = self.root / 'application'
        self.git(self.root, 'clone', MAKER_URL.removesuffix('.git'), str(self.app))

    def git(self, folder, *args):
        return subprocess.check_output(['git', '-C', str(folder), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def command(self, *args, ok=True, **extra):
        result = subprocess.run([str(self.app / 'bilibi'), *args], env={**self.env, **extra},
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0 if ok else (1 if args[1:] else 2), result.stdout + result.stderr)
        return result.stdout + result.stderr

    def calls(self):
        return self.asked.read_text().splitlines() if self.asked.exists() else []

    def remotes(self):
        return self.git(self.app, 'remote', '-v')

    def refused(self, *expected, repository='acme/shop', **extra):
        before = (self.remotes(), self.git(self.app, 'config', '--get-regexp', '^branch\\.'))
        output = self.command('own', repository, ok=False, **extra)
        for text in expected:
            self.assertIn(text, output)
        self.assertIn('nothing was changed', output.lower())
        self.assertEqual(before, (self.remotes(), self.git(self.app, 'config', '--get-regexp', '^branch\\.')))
        self.assertNotIn('repo create acme/shop --private', self.calls())
        return output

    def assert_owned(self):
        self.assertEqual(self.git(self.app, 'remote', 'get-url', 'origin'), APP_URL)
        # The exact maker address that ./bilibi upgrade and update require.
        self.assertEqual(self.git(self.app, 'remote', 'get-url', 'upstream'), MAKER_URL)
        self.assertEqual(self.git(self.app, 'rev-parse', '--abbrev-ref', 'main@{upstream}'), 'origin/main')
        self.assertEqual(self.git(self.root / 'shop.git', 'rev-parse', 'main'), self.git(self.app, 'rev-parse', 'main'))
        self.assertIn('repo set-default acme/shop', self.calls())

    def test_fresh_clone_becomes_a_new_private_repository_linked_to_the_maker(self):
        (self.app / 'LICENSE').write_text('Copyright (c) 2026 Acme. All rights reserved.\n')
        self.git(self.app, 'add', 'LICENSE')
        self.git(self.app, 'commit', '-m', 'Set application licence')
        output = self.command('own', 'acme/shop')
        self.assert_owned()
        self.assertIn('repo create acme/shop --private', self.calls())
        self.assertEqual(self.git(self.root / 'shop.git', 'show', 'main:LICENSE'), 'Copyright (c) 2026 Acme. All rights reserved.')
        # Each step prints the command it ran, so the owner learns what happened.
        for text in ('Step 1 of 3', '$ gh repo create acme/shop --private', 'Step 2 of 3',
                     '$ git remote rename origin upstream', f'$ git remote set-url upstream {MAKER_URL}',
                     f'$ git remote add origin {APP_URL}', '$ gh repo set-default acme/shop',
                     'Step 3 of 3', '$ git push --set-upstream origin main', 'This copy is now yours'):
            self.assertIn(text, output)

    def test_an_existing_empty_repository_is_used(self):
        self.git(self.root, 'init', '--bare', '-b', 'main', str(self.root / 'shop.git'))
        output = self.command('own', 'acme/shop')
        self.assert_owned()
        self.assertIn('already exists (private, empty)', output)
        self.assertFalse(any(call.startswith('repo create') for call in self.calls()))

    def test_a_repository_with_commits_is_refused(self):
        self.git(self.root, 'init', '--bare', '-b', 'main', str(self.root / 'shop.git'))
        other = self.root / 'other'
        self.git(self.root, 'clone', APP_URL, str(other))
        (other / 'README').write_text('someone else\n')
        self.git(other, 'add', '.')
        self.git(other, 'commit', '-m', 'Theirs')
        self.git(other, 'push', 'origin', 'HEAD:main')
        before = self.git(self.root / 'shop.git', 'rev-parse', 'main')
        self.refused('acme/shop already has commits on GitHub', 'Choose a new name')
        self.assertEqual(self.git(self.root / 'shop.git', 'rev-parse', 'main'), before)

    def test_origin_that_is_not_the_maker_is_refused_with_the_way_forward(self):
        self.git(self.app, 'remote', 'set-url', 'origin', 'https://github.com/acme/older.git')
        self.refused('origin is already https://github.com/acme/older.git, not the maker',
                     f'remote set-url origin {APP_URL}', 'gh repo set-default acme/shop')
        self.assertEqual(self.calls(), [])

    def test_a_clone_that_already_has_an_upstream_is_refused(self):
        self.git(self.app, 'remote', 'add', 'upstream', MAKER_URL)
        self.refused('own starts from a fresh clone')
        self.assertEqual(self.calls(), [])

    def test_a_rerun_after_a_failed_push_finishes_the_job(self):
        self.git(self.root, 'init', '--bare', '-b', 'main', str(self.root / 'shop.git'))
        (self.root / 'shop.git' / 'hooks' / 'pre-receive').write_text('#!/bin/sh\nexit 1\n')
        (self.root / 'shop.git' / 'hooks' / 'pre-receive').chmod(0o755)
        output = self.command('own', 'acme/shop', ok=False)
        self.assertIn('Step 3 of 3', output)
        (self.root / 'shop.git' / 'hooks' / 'pre-receive').unlink()
        output = self.command('own', 'acme/shop')
        self.assertIn(f'origin is already {APP_URL}, and upstream is the maker.', output)
        self.assertNotIn('$ git remote rename', output)
        self.assert_owned()
        # Run again once everything is done: nothing new happens, and it still succeeds.
        self.command('own', 'acme/shop')
        self.assert_owned()

    def test_gh_errors_other_than_not_found_stop_before_any_change(self):
        self.refused('gh could not look up acme/shop', 'Bad credentials', 'gh auth status', GH_FAILS='1')

    def test_bad_or_maker_names_are_refused(self):
        for name, text in (('shop', 'give your repository as <owner>/<name>'),
                           ('acme/shop.git', 'give your repository as <owner>/<name>'),
                           ('BelimbingProject/bilibi-app-maker', 'is the maker itself')):
            with self.subTest(name=name):
                output = self.command('own', name, ok=False)
                self.assertIn(text, output)
        self.assertIn('Use ./bilibi own <owner>/<name>', self.command('own', ok=False))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.git(self.app, 'remote'), 'origin')


if __name__ == '__main__':
    unittest.main()
