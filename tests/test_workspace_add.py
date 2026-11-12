"""Exercise add with real local Git repositories, without network configuration."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import workspace

OFFICIAL = workspace.OFFICIAL


class AddTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bilibi-add-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'application'
        self.app.mkdir()
        shutil.copy2(ROOT / 'bilibi', self.app)
        shutil.copytree(ROOT / 'scripts', self.app / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('BILIBI_SOURCE_DIR', 'BILIBI_BUILD_DIR', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')
                    and not k.startswith('GIT_CONFIG_')}
        self.env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.com',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.com')
        self.git(self.app, 'init')
        self.remote = self.root / 'component.git'
        self.remote.mkdir()
        self.git(self.remote, 'init')
        (self.remote / 'bilibi.container.toml').write_text('id = "finance"\nlayer = "domain"\n')
        self.git(self.remote, 'add', '.')
        self.git(self.remote, 'commit', '-m', 'First')
        self.first = self.git(self.remote, 'rev-parse', 'HEAD')
        (self.remote / 'notes').write_text('second commit\n')
        self.git(self.remote, 'add', '.')
        self.git(self.remote, 'commit', '-m', 'Second')
        self.second = self.git(self.remote, 'rev-parse', 'HEAD')
        self.source = self.remote.as_uri()
        self.platform = self.app / 'sources/platform'
        self.platform.mkdir(parents=True)
        self.git(self.platform, 'init')
        self.git(self.platform, 'remote', 'add', 'origin', 'https://github.com/BelimbingProject/bilibi.git')
        self.catalogue({})
        self.git(self.platform, 'add', '.')
        self.git(self.platform, 'commit', '-m', 'Platform fixture')
        head = self.git(self.platform, 'rev-parse', 'HEAD')
        (self.app / 'setup.toml').write_text(
            'format = 1\ntargets = ["x86_64-unknown-linux-gnu"]\n[platform]\n'
            f'url = "https://github.com/BelimbingProject/bilibi.git"\ncommit = "{head}"\n')
        self.folder = self.platform / 'apps/domains/finance'
        # Only the source allowlist is substituted. Clone, checkout, origin and
        # filesystem behavior use real local repositories and the command parser.
        for target, attribute, value in (
            (workspace, 'ROOT', self.app), (workspace, 'PLATFORM', self.platform),
            (workspace, 'OFFICIAL', re.compile(re.escape(self.source))),
            (workspace.recipe, 'PLATFORM', self.platform),
        ):
            patcher = mock.patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        original_recipe = self.original_recipe = workspace.recipe.recipe
        patcher = mock.patch.object(workspace.recipe, 'recipe', lambda: original_recipe(self.app))
        patcher.start()
        self.addCleanup(patcher.stop)
        original_run = workspace.recipe.run
        def local_run(*args, cwd=None, capture=False):
            return original_run(*args, cwd=cwd or self.app, capture=capture)
        patcher = mock.patch.object(workspace, 'run', local_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, folder, *args):
        return subprocess.check_output(['git', '-C', str(folder), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def catalogue(self, entries):
        (self.platform / 'catalogue.json').write_text(json.dumps(entries))

    def add(self, *args, ok=True):
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['./bilibi', 'add', 'finance', *args]), contextlib.redirect_stdout(output):
            if ok:
                workspace.main()
            else:
                with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)) as error:
                    workspace.main()
                output.write(str(error.exception))
        return output.getvalue()

    def explicit(self, commit=None, **kwargs):
        return self.add('--kind', 'domains', '--source', self.source, '--commit', commit or self.first, **kwargs)

    def test_explicit_absent_from_catalogue_and_existing_work_preserved(self):
        self.explicit()
        self.assertEqual(self.git(self.folder, 'rev-parse', 'HEAD'), self.first)
        self.git(self.folder, 'checkout', '-b', 'local-work')
        (self.folder / 'notes').write_text('staged work\n')
        self.git(self.folder, 'add', 'notes')
        (self.folder / 'notes').write_text('unstaged work\n')
        (self.folder / 'untracked').write_text('keep me\n')
        before = self.snapshot()
        self.assertIn('Keeping existing checkout, branch and edits:', self.explicit())
        self.assertEqual(before, self.snapshot())
        output = self.explicit(self.second, ok=False)
        self.assertIn(f'is not at commit {self.second}. Commit or stash its work, then run:', output)
        self.assertIn(f'git -C {self.folder} fetch origin && git -C {self.folder} checkout --detach {self.second}; nothing was changed', output)
        self.assertEqual(before, self.snapshot())

    def snapshot(self):
        return (self.git(self.folder, 'rev-parse', 'HEAD'), self.git(self.folder, 'branch', '--show-current'),
                self.git(self.folder, 'diff'), self.git(self.folder, 'diff', '--cached'),
                self.git(self.folder, 'status', '--porcelain'),
                (self.folder / 'untracked').read_bytes(), self.git(self.folder, 'remote', '-v'))

    def test_flags_validate_before_materializing(self):
        for args, message in [
            (('--kind', 'domains'), 'go together; give all three or none'),
            (('--source', self.source, '--commit', self.first), 'go together; give all three or none'),
            (('--kind', 'domains', '--source', self.source, '--commit', 'main'), 'full 40-character lowercase commit ID'),
            (('--kind', 'domains', '--source', 'https://user:token@github.com/other/finance', '--commit', self.first), 'without a user name, password, token'),
            (('--kind', 'domains', '--source', 'ssh://git@github.com/other/finance', '--commit', self.first), 'must be an HTTPS Git URL'),
        ]:
            with self.subTest(args=args):
                self.assertIn(message, self.add(*args, ok=False))
                self.assertFalse(self.folder.exists())

    def test_existing_origin_and_occupied_path_refusals(self):
        self.folder.parent.mkdir(parents=True)
        self.folder.write_text('valuable file')
        self.assertIn('is occupied; move it aside deliberately', self.explicit(ok=False))
        self.assertEqual(self.folder.read_text(), 'valuable file')
        self.folder.unlink()
        self.folder.mkdir()
        self.assertIn('is not its own Git checkout', self.explicit(ok=False))
        self.folder.rmdir()
        self.explicit()
        self.git(self.folder, 'remote', 'set-url', 'origin', 'https://github.com/BelimbingProject/other')
        self.assertIn('has a different origin; inspect it', self.explicit(ok=False))
        self.assertEqual(self.git(self.folder, 'rev-parse', 'HEAD'), self.first)

    def test_missing_commit_and_descriptor_keep_clone(self):
        self.assertIn('the new clone was kept at', self.explicit('0' * 40, ok=False))
        self.assertEqual(self.git(self.folder, 'rev-parse', 'HEAD'), self.second)
        (self.folder / 'bilibi.container.toml').write_text('id = "wrong"\nlayer = "domain"\n')
        self.assertIn('does not match the requested component; checkout retained for inspection',
                      self.explicit(self.second, ok=False))

    def test_extensions_mount(self):
        (self.remote / 'bilibi.container.toml').write_text('id = "finance"\nlayer = "extension"\n')
        self.git(self.remote, 'commit', '-am', 'Extension fixture')
        head = self.git(self.remote, 'rev-parse', 'HEAD')
        self.add('--kind', 'extensions', '--source', self.source, '--commit', head)
        self.assertEqual(self.git(self.platform / 'apps/extensions/finance', 'rev-parse', 'HEAD'), head)

    def test_source_without_git_suffix_is_savable(self):
        source = 'https://github.com/BelimbingProject/finance'
        get_url = workspace.run
        def raw_origin(*args, **kwargs):
            if args[:3] == ('git', 'remote', 'get-url'):
                args = ('git', 'config', '--get', f'remote.{args[3]}.url')
            return get_url(*args, **kwargs)
        rewrite = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': f'url.{self.source}.insteadOf',
                   'GIT_CONFIG_VALUE_0': source + '.git'}
        with (mock.patch.object(workspace, 'OFFICIAL', OFFICIAL), mock.patch.object(workspace, 'run', raw_origin),
              mock.patch.dict(os.environ, rewrite)):
            self.add('--kind', 'domains', '--source', source, '--commit', self.first)
            self.assertEqual(self.git(self.folder, 'config', '--get', 'remote.origin.url'), source + '.git')
            (self.platform / '.git/info/exclude').write_text('/apps/domains/\n')
            recorded = self.app / 'recorded'
            recorded.mkdir()
            (recorded / 'setup.toml').write_text(workspace.serialize(workspace.selected_recipe()))
            _, sources = self.original_recipe(recorded)
        self.assertIn(('apps/domains/finance', {'id': 'finance', 'url': source + '.git', 'commit': self.first}), sources)


if __name__ == '__main__':
    unittest.main()
