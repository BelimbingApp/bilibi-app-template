"""Exercise ./bilibi connect-private-sources with a real Git application, a scripted gh and a scripted browser.

gh answers from a JSON file that stands in for GitHub and records every call.
The browser plays the owner's part: it loads the local page, reads the form that
would go to GitHub, and returns to the redirect URL with a code, as GitHub does
after the owner chooses "Create GitHub App".
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PLATFORM = 'https://github.com/BelimbingProject/bilibi.git'
PEM = '-----BEGIN RSA PRIVATE KEY-----\nnot-a-real-key\n-----END RSA PRIVATE KEY-----\n'

FAKE_GH = r'''
import json, os, sys
state_path = os.environ['FAKE_GITHUB']
github = json.load(open(state_path))
args = sys.argv[1:]
stdin = None if sys.stdin.isatty() else sys.stdin.read()
github['calls'].append({'args': args, 'stdin': stdin})

def finish(output=None, code=0, error=''):
    json.dump(github, open(state_path, 'w'))
    if output is not None:
        print(json.dumps(output))
    if error:
        print(error, file=sys.stderr)
    sys.exit(code)

def option(name):
    return args[args.index(name) + 1]

if args[0] in github.get('failing', []):
    finish(code=1, error='gh: Resource not accessible (HTTP 403)')
if args[0] == 'api':
    path = args[-1]
    if path == 'user':
        finish(github['user'])
    if path.startswith('users/'):
        account = github['accounts'].get(path.removeprefix('users/'))
        finish(account) if account else finish(code=1, error='gh: Not Found (HTTP 404)')
    if '--method' in args and path.startswith('app-manifests/'):
        if path != f'app-manifests/{github["code"]}/conversions':
            finish(code=1, error='gh: Not Found (HTTP 404)')
        finish(github['app'])
    if '/actions/variables/' in path:
        repository, name = path.removeprefix('repos/').split('/actions/variables/')
        value = github['variables'].get(repository, {}).get(name)
        finish({'name': name, 'value': value}) if value is not None else finish(code=1, error='gh: Not Found (HTTP 404)')
    if path.startswith('repos/'):
        facts = github['repositories'].get(path.removeprefix('repos/'))
        finish(facts) if facts else finish(code=1, error='gh: Not Found (HTTP 404)')
if args[:2] == ['secret', 'set']:
    github['secrets'].setdefault(option('--repo'), {})[args[2]] = stdin
    finish()
if args[:2] == ['variable', 'set']:
    github['variables'].setdefault(option('--repo'), {})[args[2]] = option('--body')
    finish()
if args[:2] == ['variable', 'delete']:
    github['variables'][option('--repo')].pop(args[2])
    finish()
finish(code=2, error=f'fake gh: unexpected call {args}')
'''

FAKE_BROWSER = r'''
import json, os, re, sys, html, urllib.request
from urllib.parse import parse_qs, urlsplit
url = sys.argv[1]
with open(os.environ['BROWSER_LOG'], 'a') as log:
    log.write(url + '\n')
if url.startswith('http://127.0.0.1:'):
    page = urllib.request.urlopen(url).read().decode()
    action = html.unescape(re.search(r'action="([^"]+)"', page)[1])
    manifest = json.loads(html.unescape(re.search(r'name="manifest" value="([^"]+)"', page)[1]))
    json.dump({'action': action, 'manifest': manifest}, open(os.environ['BROWSER_FORM'], 'w'))
    state = parse_qs(urlsplit(action).query)['state'][0]
    code = json.load(open(os.environ['FAKE_GITHUB']))['code']
    try:
        # A stray or forged return is ignored; the command keeps waiting.
        urllib.request.urlopen(f'{manifest["redirect_url"]}?code={code}&state=forged')
    except Exception:
        pass
    urllib.request.urlopen(f'{manifest["redirect_url"]}?code={code}&state={state}')
'''


def repository(full_name, identity, private=True, owner_type='Organization', owner_id=900):
    owner = full_name.split('/')[0]
    return {'full_name': full_name, 'id': identity, 'private': private,
            'owner': {'login': owner, 'id': owner_id, 'type': owner_type}}


class ConnectPrivateSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bilibi-private-sources-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tools = self.root / 'bin'
        tools.mkdir()
        (tools / 'gh').write_text(f'#!{sys.executable}\n{FAKE_GH}')
        (tools / 'gh').chmod(0o755)
        browser = self.root / 'browser'
        browser.write_text(f'#!{sys.executable}\n{FAKE_BROWSER}')
        browser.chmod(0o755)
        self.state = self.root / 'github.json'
        self.form = self.root / 'form.json'
        self.opened = self.root / 'opened'
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key not in
                    ('BILIBI_SOURCE_DIR', 'BILIBI_BUILD_DIR', 'CARGO_TARGET_DIR', 'CI', 'GITHUB_ACTIONS')}
        self.env.update(GIT_CONFIG_GLOBAL=str(self.root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test',
                        PATH=f'{tools}{os.pathsep}{self.env["PATH"]}', BROWSER=str(browser),
                        FAKE_GITHUB=str(self.state), BROWSER_FORM=str(self.form), BROWSER_LOG=str(self.opened))
        # The owner's application: a real Git checkout whose origin is their GitHub repository.
        self.app = self.root / 'application'
        self.app.mkdir()
        shutil.copy2(ROOT / 'bilibi', self.app)
        shutil.copy2(ROOT / '.gitignore', self.app)
        shutil.copytree(ROOT / 'scripts', self.app / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
        self.recipe(('domains', 'ledger', 'https://github.com/acme/ledger.git'),
                    ('domains', 'finance', 'https://github.com/BelimbingProject/finance.git'),
                    ('extensions', 'pricing', 'https://github.com/acme/pricing.git'))
        self.git('init', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-m', 'Application')
        self.git('remote', 'add', 'origin', 'git@github.com:acme/shop.git')
        self.github({
            'calls': [], 'code': 'one-time-code', 'failing': [],
            'user': {'login': 'owner-person'},
            'accounts': {'acme': {'login': 'acme', 'id': 900, 'type': 'Organization'}},
            'repositories': {
                'BelimbingProject/bilibi': repository('BelimbingProject/bilibi', 1, private=False),
                'BelimbingProject/finance': repository('BelimbingProject/finance', 2, private=False),
                'acme/ledger': repository('acme/ledger', 31),
                'acme/pricing': repository('acme/pricing', 32),
            },
            'variables': {}, 'secrets': {},
            'app': {'slug': 'shop-sources-1a2b3c', 'client_id': 'Iv23liClientId', 'pem': PEM,
                    'owner': {'login': 'acme'}, 'html_url': 'https://github.com/apps/shop-sources-1a2b3c'},
        })

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.app), *args], env=self.env, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def recipe(self, *sources):
        lines = ['format = 1', 'targets = ["x86_64-unknown-linux-gnu"]', '', '[platform]',
                 f'url = "{PLATFORM}"', f'commit = "{"a" * 40}"']
        for layer, identity, url in sources:
            lines += ['', f'[[{layer}]]', f'id = "{identity}"', f'url = "{url}"', f'commit = "{"b" * 40}"']
        (self.app / 'setup.toml').write_text('\n'.join(lines) + '\n')

    def github(self, value=None):
        if value is not None:
            self.state.write_text(json.dumps(value))
        return json.loads(self.state.read_text())

    def change(self, edit):
        state = self.github()
        edit(state)
        self.github(state)

    def command(self, answer='y\n', ok=True, **extra):
        result = subprocess.run([str(self.app / 'bilibi'), 'connect-private-sources'], env={**self.env, **extra},
                                cwd=self.root, input=answer, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertNotIn('not-a-real-key', output)
        return output

    def writes(self):
        return [call['args'][:3] for call in self.github()['calls'] if call['args'][0] in ('secret', 'variable')]

    def refused(self, *expected, answer='y\n', **extra):
        output = self.command(answer, ok=False, **extra)
        for text in expected:
            self.assertIn(text, output)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.form.exists())
        return output

    def test_creates_the_app_stores_it_and_opens_the_install_page_for_exactly_the_private_sources(self):
        output = self.command('y\n')
        github = self.github()
        # The manifest asks for one permission, no webhook, and installs only on acme.
        form = json.loads(self.form.read_text())
        action = urlsplit(form['action'])
        self.assertEqual(f'{action.scheme}://{action.netloc}{action.path}', 'https://github.com/organizations/acme/settings/apps/new')
        manifest = form['manifest']
        self.assertEqual(manifest['default_permissions'], {'contents': 'read'})
        self.assertEqual(manifest['default_events'], [])
        self.assertIs(manifest['hook_attributes']['active'], False)
        self.assertIs(manifest['public'], False)
        self.assertTrue(manifest['redirect_url'].startswith('http://127.0.0.1:'))
        self.assertLessEqual(len(manifest['name']), 34)
        self.assertEqual(manifest['url'], 'https://github.com/acme/shop')
        # The key reached gh only through its input, never its arguments, and went to this repository.
        self.assertEqual(github['secrets'], {'acme/shop': {'SOURCE_APP_PRIVATE_KEY': PEM}})
        self.assertFalse(any('not-a-real-key' in ' '.join(call['args']) for call in github['calls']))
        self.assertEqual(github['variables'], {'acme/shop': {'SOURCE_APP_CLIENT_ID': 'Iv23liClientId',
                                                             'SOURCE_READ_ON_PULL_REQUESTS': 'true'}})
        install = self.opened.read_text().splitlines()[-1]
        address = urlsplit(install)
        self.assertEqual(address.path, '/apps/shop-sources-1a2b3c/installations/new/permissions')
        self.assertEqual(parse_qs(address.query), {'target_id': ['900'], 'repository_ids[]': ['31', '32']})
        for text in ('acme/ledger', 'acme/pricing', 'Only select repositories', 'not written to disk',
                     'checked fully too', install):
            self.assertIn(text, output)
        self.assertNotIn('BelimbingProject/finance', output)
        # Nothing new in the application: no key file, no tracked change.
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=all'), '')
        self.assertFalse([path for path in self.root.rglob('*') if path.is_file() and path != self.state
                          and 'not-a-real-key' in path.read_text(errors='ignore')])

    def test_long_repository_name_keeps_the_app_name_within_the_limit(self):
        self.git('remote', 'set-url', 'origin', 'git@github.com:acme/my-long-application-name-for-shop.git')
        self.command('n\n')
        name = json.loads(self.form.read_text())['manifest']['name']
        self.assertLessEqual(len(name), 34)
        self.assertTrue(name.startswith('my-long-application'))

    def test_no_keeps_pull_requests_without_the_token(self):
        self.change(lambda github: github['variables'].update({'acme/shop': {'SOURCE_READ_ON_PULL_REQUESTS': 'true'}}))
        output = self.command('maybe\nn\n')
        self.assertEqual(self.github()['variables'], {'acme/shop': {'SOURCE_APP_CLIENT_ID': 'Iv23liClientId'}})
        self.assertIn('the check of main after a merge is the one that counts', output)

    def test_personal_account_uses_the_personal_settings_page(self):
        def personal(github):
            github['accounts']['acme']['type'] = 'User'
            github['user']['login'] = 'acme'
        self.change(personal)
        self.command('n\n')
        self.assertTrue(json.loads(self.form.read_text())['action'].startswith('https://github.com/settings/apps/new?state='))
        self.assertNotIn('SOURCE_READ_ON_PULL_REQUESTS', self.github()['variables']['acme/shop'])

    def test_another_persons_account_is_refused(self):
        self.change(lambda github: github['accounts']['acme'].update(type='User'))
        self.refused('Only acme can create a GitHub App', 'signed in to gh as owner-person')

    def test_public_sources_need_nothing(self):
        self.recipe(('domains', 'finance', 'https://github.com/BelimbingProject/finance.git'))
        output = self.command('')
        self.assertIn('no private GitHub sources', output)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.opened.exists())

    def test_an_existing_app_is_not_replaced(self):
        self.change(lambda github: github['variables'].update({'acme/shop': {'SOURCE_APP_CLIENT_ID': 'Iv23liOld'}}))
        self.refused('already reads private sources with a GitHub App (client ID Iv23liOld)',
                     'gh variable delete SOURCE_APP_CLIENT_ID --repo acme/shop')

    def test_private_source_of_another_account_is_refused(self):
        self.change(lambda github: github['repositories'].update(
            {'acme/pricing': repository('other/pricing', 32, owner_id=901)}))
        self.refused('apps/extensions/pricing (other/pricing): private, and owned by another account than acme',
                     'SOURCE_READ_TOKEN')

    def test_unreadable_source_is_named(self):
        self.change(lambda github: github['repositories'].pop('acme/ledger'))
        self.refused('apps/domains/ledger: cannot see acme/ledger', 'Nothing was changed')

    def test_missing_answer_stops_before_anything_is_created(self):
        self.refused('No answer was received', answer='')

    def test_failed_key_store_says_how_to_finish_and_keeps_nothing(self):
        self.change(lambda github: github['failing'].append('secret'))
        output = self.command(ok=False)
        for text in ('https://github.com/organizations/acme/settings/apps/shop-sources-1a2b3c',
                     'generate a private key', 'gh secret set SOURCE_APP_PRIVATE_KEY --repo acme/shop',
                     'gh variable set SOURCE_APP_CLIENT_ID --repo acme/shop --body Iv23liClientId',
                     'not saved anywhere', '/apps/shop-sources-1a2b3c/installations/new/permissions'):
            self.assertIn(text, output)
        self.assertEqual(self.github()['variables'], {})

    def test_app_created_for_another_owner_is_not_stored(self):
        self.change(lambda github: github['app']['owner'].update(login='owner-person'))
        output = self.command(ok=False)
        self.assertIn('belongs to owner-person, not acme', output)
        self.assertEqual(self.writes(), [])

    def test_origin_must_be_the_owners_github_repository(self):
        for url in ('https://github.com/BelimbingProject/bilibi-app-maker.git', str(self.root / 'shop.git')):
            with self.subTest(url=url):
                self.git('remote', 'set-url', 'origin', url)
                self.refused('origin must be your own application repository')

    def test_ci_is_refused(self):
        self.refused('opens GitHub in your browser', CI='true')

    def test_help_lists_the_command(self):
        result = subprocess.run([str(self.app / 'bilibi'), 'help'], env=self.env, capture_output=True, text=True)
        self.assertIn('  connect-private-sources\n', result.stdout)


if __name__ == '__main__':
    unittest.main()
