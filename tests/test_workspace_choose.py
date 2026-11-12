"""Exercise the guided Domain and Extension list with real Git remotes and a real terminal."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import choose
import workspace

FINANCE = 'https://github.com/BelimbingProject/bilibi-dom-finance'
CATALOGUE = {
    'format': 1,
    'domains': {
        'people': {'name': 'People', 'description': 'Employment, leave and payroll.',
                   'repository': 'https://github.com/BelimbingProject/bilibi-dom-people'},
        'finance': {'name': 'Finance', 'description': "A company's bookkeeping:\n the ledger and reports.",
                    'repository': FINANCE},
    },
    'extensions': {
        'bank_feeds': {'name': 'Bank feeds', 'description': 'Connects bank statements.',
                       'repository': 'https://github.com/BelimbingProject/bilibi-ext-bank-feeds'},
    },
}


def keys(*names):
    return iter(names)


class ListTest(unittest.TestCase):
    def setUp(self):
        self.choices = choose.options(CATALOGUE)

    def test_lists_catalogue_order_and_skips_added(self):
        self.assertEqual([entry[1] for entry in self.choices], ['people', 'finance', 'bank_feeds'])
        self.assertEqual(self.choices[1][3], "A company's bookkeeping: the ledger and reports.")
        self.assertEqual([entry[1] for entry in choose.options(CATALOGUE, {'apps/domains/finance'})],
                         ['people', 'bank_feeds'])
        self.assertEqual(choose.listing(self.choices, width=200), [
            'Domains:',
            '   1. People (people) - Employment, leave and payroll.',
            "   2. Finance (finance) - A company's bookkeeping: the ledger and reports.",
            'Extensions:',
            '   3. Bank feeds (bank_feeds) - Connects bank statements.',
        ])
        for line in choose.listing(self.choices, cursor=0, width=30):
            self.assertLessEqual(len(line), 29)

    def test_decodes_terminal_input(self):
        self.assertEqual(list(choose.decode('\x1b[B\x1b[A \r')), ['down', 'up', 'space', 'enter'])
        self.assertEqual(list(choose.decode('\x1bOBj12y')), ['down', 'down', '1', '2', 'y'])
        self.assertEqual(list(choose.decode('\x1b')), ['quit'])
        self.assertEqual(list(choose.decode('\x1b[5~')), [])

    def test_arrows_and_space(self):
        out = io.StringIO()
        picked = choose.pick(self.choices, keys('down', 'space', 'down', 'space', 'space', 'enter'), out)
        self.assertEqual([entry[1] for entry in picked], ['finance'])
        self.assertIn('> [x]  2. Finance', out.getvalue())

    def test_numbers_and_cancel(self):
        self.assertEqual([entry[1] for entry in choose.pick(self.choices, keys('3', '1', 'enter'), io.StringIO())],
                         ['people', 'bank_feeds'])
        self.assertIsNone(choose.pick(self.choices, keys('2', 'quit'), io.StringIO()))
        self.assertEqual(choose.pick(self.choices, keys('9', 'enter'), io.StringIO()), [])

    def test_two_digit_numbers_wait_for_the_next_key(self):
        many = [('domains', f'd{n}', f'D{n}', '') for n in range(1, 13)]
        picked = choose.pick(many, keys('1', '2', '1', 'space', 'enter'), io.StringIO())
        self.assertEqual([entry[1] for entry in picked], ['d1', 'd12'])
        # Enter after a pending number only settles the number; a second Enter goes on.
        picked = choose.pick(many, keys('1', 'enter', 'enter'), io.StringIO())
        self.assertEqual([entry[1] for entry in picked], ['d1'])

    def test_no_terminal_prints_list_and_adds_nothing(self):
        output = io.StringIO()
        add = mock.Mock()
        with mock.patch.object(choose, 'interactive', return_value=False), contextlib.redirect_stdout(output):
            self.assertEqual(choose.guided(CATALOGUE, set(), add), [])
        add.assert_not_called()
        self.assertIn('2. Finance (finance)', output.getvalue())
        self.assertIn('No terminal, so nothing was added. Add one with ./bilibi add <id>', output.getvalue())

    def test_nothing_added_until_confirmed(self):
        for answer in ('n', 'quit'):
            add = mock.Mock()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(choose.guided(CATALOGUE, set(), add, keys('2', 'enter', answer), io.StringIO()), [])
            add.assert_not_called()
            self.assertIn('Cancelled; nothing was added.', output.getvalue())

    def test_first_refusal_stops_and_keeps_earlier_adds(self):
        def add(component):
            if component == 'bank_feeds':
                raise ValueError('bank_feeds: refused')
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaisesRegex(ValueError, 'refused'):
            choose.guided(CATALOGUE, set(), add, keys('2', '3', 'enter', 'y'), io.StringIO())
        self.assertIn('Added before the problem below: finance. They stay in place.', errors.getvalue())


class TerminalTest(unittest.TestCase):
    def test_real_terminal_arrow_keys_and_confirm(self):
        """Run the picker on a pseudo-terminal, so cbreak mode and escape sequences are real."""
        program = textwrap.dedent(f'''
            import sys
            sys.path.insert(0, {str(ROOT / 'scripts')!r})
            import json
            import choose
            print('ADDED', choose.guided(json.loads({json.dumps(CATALOGUE)!r}), set(),
                                         lambda component: print('ADD', component)))
        ''')
        child, terminal = pty.fork()
        if child == 0:
            os.environ.pop('CI', None)
            os.execv(sys.executable, [sys.executable, '-c', program])
        output = b''

        def read_until(text):
            nonlocal output
            deadline = time.monotonic() + 20
            while text.encode() not in output:
                self.assertLess(time.monotonic(), deadline, output.decode(errors='replace'))
                if select.select([terminal], [], [], 0.2)[0]:
                    try:
                        output += os.read(terminal, 4096)
                    except OSError:
                        break
        try:
            read_until('Enter goes on')
            os.write(terminal, b'\x1b[B')
            read_until('>')
            os.write(terminal, b' ')
            read_until('[x]')
            os.write(terminal, b'\r')
            read_until('Press y to add')
            os.write(terminal, b'y')
            read_until("ADDED ['finance']")
        finally:
            os.close(terminal)
            os.waitpid(child, 0)
        self.assertIn(b'ADD finance', output)


class ChooseCommandTest(unittest.TestCase):
    """./bilibi choose and the first start, against a real local Domain remote."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bilibi-choose-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'application'
        self.app.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('BILIBI_SOURCE_DIR', 'BILIBI_BUILD_DIR', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'CI')
                    and not k.startswith('GIT_CONFIG_')}
        self.remote = self.root / 'finance.git'
        self.remote.mkdir()
        self.env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.com',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.com',
                        # The catalogue names the official URL; Git serves it from the throwaway remote.
                        GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0=f'url.{self.remote.as_uri()}.insteadOf',
                        GIT_CONFIG_VALUE_0=FINANCE + '.git')
        self.git(self.app, 'init')
        self.git(self.remote, 'init')
        (self.remote / 'bilibi.container.toml').write_text('id = "finance"\nlayer = "domain"\n')
        self.git(self.remote, 'add', '.')
        self.git(self.remote, 'commit', '-m', 'Finance')
        self.finance = self.git(self.remote, 'rev-parse', 'HEAD')
        self.platform = self.app / 'sources/platform'
        self.platform.mkdir(parents=True)
        self.git(self.platform, 'init')
        self.git(self.platform, 'remote', 'add', 'origin', 'https://github.com/BelimbingProject/bilibi.git')
        (self.platform / 'catalogue.json').write_text(json.dumps(CATALOGUE))
        self.git(self.platform, 'add', '.')
        self.git(self.platform, 'commit', '-m', 'Platform fixture')
        (self.app / 'setup.toml').write_text(
            'format = 1\ntargets = ["x86_64-unknown-linux-gnu"]\n[platform]\n'
            f'url = "https://github.com/BelimbingProject/bilibi.git"\ncommit = "{self.git(self.platform, "rev-parse", "HEAD")}"\n')
        self.folder = self.platform / 'apps/domains/finance'
        original_recipe = workspace.recipe.recipe
        original_run = workspace.recipe.run
        self.started = []
        for target, attribute, value in (
            (workspace, 'ROOT', self.app), (workspace, 'PLATFORM', self.platform),
            (workspace.recipe, 'PLATFORM', self.platform),
            (workspace.recipe, 'recipe', lambda: original_recipe(self.app)),
            (workspace, 'run', lambda *args, cwd=None, capture=False: original_run(*args, cwd=cwd or self.app, capture=capture)),
            # Only the expensive tool installation and the launcher handoff are replaced.
            (workspace, 'setup', lambda: self.started.append('setup')),
            (workspace, 'status', lambda: None),
            (workspace.os, 'execvp', lambda *args: self.started.append('launcher')),
        ):
            patcher = mock.patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.chdir, os.getcwd())

    def git(self, folder, *args):
        return subprocess.check_output(['git', '-C', str(folder), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def bilibi(self, command, *pressed, terminal=True):
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['./bilibi', command]), \
                mock.patch.object(choose, 'interactive', return_value=terminal), \
                mock.patch.object(choose, 'keys', return_value=contextlib.nullcontext(iter(pressed))), \
                contextlib.redirect_stdout(output):
            workspace.main()
        return output.getvalue()

    def test_choose_adds_finance_from_the_list_and_starts(self):
        output = self.bilibi('choose', 'down', 'space', 'enter', 'y')
        self.assertIn('2. Finance (finance)', output)
        self.assertEqual(self.git(self.folder, 'rev-parse', 'HEAD'), self.finance)
        self.assertEqual(self.started, ['setup', 'launcher'])
        # Finance is added now, so the list offers only the rest.
        self.started.clear()
        output = self.bilibi('choose', 'quit')
        self.assertNotIn('Finance (finance)', output)
        self.assertIn('Cancelled; nothing was added.', output)
        self.assertEqual(self.started, [])

    def test_choose_without_terminal_changes_nothing(self):
        before = self.git(self.platform, 'status', '--porcelain', '--ignored')
        output = self.bilibi('choose', terminal=False)
        self.assertIn('No terminal, so nothing was added.', output)
        self.assertFalse(self.folder.exists())
        self.assertEqual(before, self.git(self.platform, 'status', '--porcelain', '--ignored'))
        self.assertEqual(self.started, [])

    def test_first_start_offers_the_list_once(self):
        output = self.bilibi('start', '2', 'enter', 'y')
        self.assertIn('Choose the business features to add.', output)
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(self.started, ['setup', 'launcher'])
        shutil.rmtree(self.folder)
        self.started.clear()
        output = self.bilibi('start', 'enter')
        self.assertNotIn('Choose the business features', output)
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.started, ['setup', 'launcher'])

    def test_first_start_without_terminal_only_hints(self):
        output = self.bilibi('start', terminal=False)
        self.assertIn('./bilibi choose lists them', output)
        self.assertFalse((self.app / '.local/guided-start').exists())
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.started, ['setup', 'launcher'])

    def test_application_with_saved_components_is_not_new(self):
        with (self.app / 'setup.toml').open('a') as stream:
            stream.write(f'[[domains]]\nid = "finance"\nurl = "{FINANCE}.git"\ncommit = "{self.finance}"\n')
        with mock.patch.object(choose, 'guided') as guided:
            self.bilibi('start', terminal=True)
        guided.assert_not_called()

    def test_add_keeps_working(self):
        with mock.patch.object(sys, 'argv', ['./bilibi', 'add', 'finance']), contextlib.redirect_stdout(io.StringIO()):
            workspace.main()
        self.assertEqual(self.git(self.folder, 'rev-parse', 'HEAD'), self.finance)


if __name__ == '__main__':
    unittest.main()
