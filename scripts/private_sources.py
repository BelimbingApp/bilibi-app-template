"""./bilibi connect-private-sources: let the application's GitHub checks read its private sources.

The checks read private Domains and Extensions with a GitHub App that only the
application's owner can install, with one permission: read repository contents.
This command creates that App from a manifest (the owner confirms once on
GitHub), stores its client ID and private key in the application repository's
Actions settings with gh, and opens the App's install page for exactly the
private source repositories. The private key goes from GitHub to gh in memory:
it is never written to disk or printed.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import recipe

COMMAND = './bilibi connect-private-sources'
GUIDE = 'docs/guides/private-sources.md'
CLIENT_ID, PRIVATE_KEY = 'SOURCE_APP_CLIENT_ID', 'SOURCE_APP_PRIVATE_KEY'
PULL_REQUESTS = 'SOURCE_READ_ON_PULL_REQUESTS'
GITHUB_SOURCE = re.compile(r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+?)\.git')
# The manifest code GitHub returns is valid for one hour.
WAIT_SECONDS = 3600


def gh(*args, stdin=None):
    """Run gh; return (succeeded, stdout). Its own error output reaches the terminal."""
    # gh never reads the terminal: the answers typed here are for this command.
    given = {'input': stdin} if stdin is not None else {'stdin': subprocess.DEVNULL}
    result = subprocess.run(['gh', *args], **given, stdout=subprocess.PIPE, text=True, cwd=recipe.ROOT)
    return result.returncode == 0, result.stdout


def gh_json(*args):
    succeeded, output = gh('api', *args)
    return json.loads(output) if succeeded else None


def variable(repository, name):
    """The repository's Actions variable, or None when it is not set."""
    found = subprocess.run(['gh', 'api', f'repos/{repository}/actions/variables/{name}'], cwd=recipe.ROOT,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if found.returncode == 0:
        return json.loads(found.stdout)['value']
    if 'HTTP 404' in found.stderr:
        return None
    raise ValueError(f'Cannot read the Actions variables of {repository} ({found.stderr.strip()}). '
                     f'Sign in with gh auth login as someone who administers {repository}, then rerun {COMMAND}.')


def private_sources(owner):
    """The private GitHub repositories in setup.toml, which the App must be able to read."""
    _, sources = recipe.recipe()
    private, foreign = {}, []
    for path, source in sources:
        match = GITHUB_SOURCE.fullmatch(source['url'])
        if not match:
            print(f'{path}: {source["url"]} is not on GitHub, so a GitHub App cannot read it; '
                  f'see "Sources on other hosts" in {GUIDE}.')
            continue
        facts = gh_json(f'repos/{match[1]}')
        if facts is None:
            raise ValueError(f'{path}: cannot see {match[1]} with your GitHub sign-in. Check gh auth status and '
                             f'that you can open https://github.com/{match[1]}, then rerun {COMMAND}. Nothing was changed.')
        if not facts['private']:
            continue
        if facts['owner']['login'].lower() != owner.lower():
            foreign.append(f'{path} ({facts["full_name"]})')
        else:
            private[facts['full_name']] = facts
    if foreign:
        raise ValueError(f'{", ".join(foreign)}: private, and owned by another account than {owner}. The checks read '
                         f'private sources with a GitHub App installed on {owner}, so move these repositories to '
                         f'{owner}, or give the checks a SOURCE_READ_TOKEN instead ({GUIDE}). Nothing was changed.')
    return list(private.values())


def ask(question):
    answer = ''
    while answer not in ('y', 'yes', 'n', 'no'):
        try:
            answer = input(question).strip().lower()
        except EOFError:
            raise ValueError(f'No answer was received; nothing was changed. Run {COMMAND} in a terminal.') from None
    return answer in ('y', 'yes')


def manifest(repository, redirect):
    owner, name = repository.split('/')
    home = f'https://github.com/{repository}'
    return {
        # App names are unique across GitHub and at most 34 characters: ' sources ' and 6 hex digits take 15, leaving 19.
        'name': f'{name[:19].rstrip("-_.")} sources {secrets.token_hex(3)}',
        'url': home,
        'description': f'Lets the checks of {repository} read its private Domains and Extensions.',
        # No webhook: the App only mints read tokens for the checks.
        'hook_attributes': {'url': home, 'active': False},
        'redirect_url': redirect,
        # Installable only on the account that owns it.
        'public': False,
        'default_permissions': {'contents': 'read'},
        'default_events': [],
    }


def created_app(repository, account):
    """Open GitHub's App creation page and wait for the one-time code it sends back."""
    owner = repository.split('/')[0]
    state = secrets.token_urlsafe(24)
    received = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            address = urlsplit(self.path)
            query = parse_qs(address.query)
            if address.path == '/':
                body = form
            elif address.path == '/created' and query.get('state') == [state] and query.get('code'):
                received['code'] = query['code'][0]
                done.set()
                body = '<p>The GitHub App is created. Return to your terminal.</p>'
            else:
                self.send_error(404)
                return
            data = f'<!doctype html><meta charset="utf-8"><title>Bilibi</title>{body}'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    # Only this computer can reach the page that hands over the code.
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    local = f'http://127.0.0.1:{server.server_address[1]}/'
    where = (f'https://github.com/organizations/{owner}/settings/apps/new' if account == 'Organization'
             else 'https://github.com/settings/apps/new')
    description = json.dumps(manifest(repository, local + 'created'))
    form = (f'<form method="post" action="{html.escape(where)}?{urlencode({"state": state})}">'
            f'<input type="hidden" name="manifest" value="{html.escape(description)}">'
            f'<p>Bilibi creates a GitHub App that can only read the private sources of {html.escape(repository)}.</p>'
            '<button>Continue to GitHub</button></form><script>document.forms[0].submit()</script>')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(f'Opening your browser to create the GitHub App. If it does not open, visit {local}\n'
              f'On GitHub, check the App is for {owner}, then choose "Create GitHub App".', flush=True)
        webbrowser.open(local)
        if not done.wait(WAIT_SECONDS):
            raise ValueError(f'GitHub did not send the App back within an hour; nothing was changed. Rerun {COMMAND}.')
    except KeyboardInterrupt:
        raise ValueError(f'Stopped before the App was created; nothing was changed in {repository}.') from None
    finally:
        server.shutdown()
        server.server_close()
    app = gh_json('--method', 'POST', f'app-manifests/{received["code"]}/conversions')
    if app is None:
        raise ValueError(f'GitHub did not hand over the new App. If it appears in your GitHub App settings, '
                         f'delete it there, then rerun {COMMAND}. Nothing was changed in {repository}.')
    return app


def save(repository, app, settings, install):
    """Store what the checks need; the key travels from memory to gh's input only."""
    set_id = f'gh variable set {CLIENT_ID} --repo {repository} --body {app["client_id"]}'
    then = f'Then install the App on exactly the private sources: {install}'
    if not gh('secret', 'set', PRIVATE_KEY, '--repo', repository, stdin=app.pop('pem'))[0]:
        raise ValueError(f'The App {app["slug"]} exists, but {repository} could not store its private key (gh said why '
                         'above); the key GitHub sent was not saved anywhere. On '
                         f'{settings}, generate a private key, run gh secret set {PRIVATE_KEY} --repo {repository} '
                         f'< <downloaded .pem>, delete the downloaded file, and run {set_id}. {then}')
    if not gh('variable', 'set', CLIENT_ID, '--repo', repository, '--body', app['client_id'])[0]:
        raise ValueError(f'The App {app["slug"]} and its private key are stored, but {CLIENT_ID} was not set (gh said '
                         f'why above); run {set_id}. {then}')


def connect(repository):
    if not shutil.which('gh'):
        raise ValueError(f'Install the GitHub CLI (gh) and sign in with gh auth login, then rerun {COMMAND}.')
    owner = repository.split('/')[0]
    existing = variable(repository, CLIENT_ID)
    if existing:
        raise ValueError(f'{repository} already reads private sources with a GitHub App (client ID {existing}). '
                         'To let it read another private repository, add that repository to the App on the '
                         f'Installed GitHub Apps page of {owner}. To replace the App, run gh variable delete '
                         f'{CLIENT_ID} --repo {repository}, then rerun {COMMAND}.')
    sources = private_sources(owner)
    if not sources:
        print('setup.toml has no private GitHub sources, so the checks need no extra access. Nothing was changed.')
        return
    account = gh_json(f'users/{owner}')
    signed_in = gh_json('user')
    if account is None or signed_in is None:
        raise ValueError(f'Cannot look up {owner} on GitHub; check gh auth status, then rerun {COMMAND}.')
    if account['type'] != 'Organization' and signed_in['login'].lower() != owner.lower():
        raise ValueError(f'Only {owner} can create a GitHub App for their account; you are signed in to gh as '
                         f'{signed_in["login"]}. Ask {owner} to run {COMMAND}. Nothing was changed.')
    names = [source['full_name'] for source in sources]
    print('Private sources the checks must read:\n' + ''.join(f'  {name}\n' for name in names), end='')
    # Asked first, so a missing answer stops before anything is created.
    pull_requests = ask(f'A pull request\'s code can print the read token. Can everyone who can push a branch to '
                        f'{repository} read all of these? Answer y to check pull requests fully too. (y/n) ')
    app = created_app(repository, account['type'])
    settings = (f'https://github.com/organizations/{owner}/settings/apps/{app["slug"]}' if account['type'] == 'Organization'
                else f'https://github.com/settings/apps/{app["slug"]}')
    install = (f'https://github.com/apps/{app["slug"]}/installations/new/permissions?'
               + urlencode([('target_id', account['id'])] + [('repository_ids[]', source['id']) for source in sources]))
    if app['owner']['login'].lower() != owner.lower():
        raise ValueError(f'The new App {app["slug"]} belongs to {app["owner"]["login"]}, not {owner}, so the checks of '
                         f'{repository} cannot use it. Delete it in the GitHub App settings of {app["owner"]["login"]}, '
                         f'then rerun {COMMAND} and keep {owner} as the owner. Nothing was changed in {repository}.')
    save(repository, app, settings, install)
    if pull_requests:
        if not gh('variable', 'set', PULL_REQUESTS, '--repo', repository, '--body', 'true')[0]:
            raise ValueError(f'The App is stored, but {PULL_REQUESTS} was not set; run gh variable set '
                             f'{PULL_REQUESTS} --repo {repository} --body true, then install the App: {install}')
    elif variable(repository, PULL_REQUESTS) is not None:
        if not gh('variable', 'delete', PULL_REQUESTS, '--repo', repository)[0]:
            raise ValueError(f'The App is stored, but pull requests still receive the token; run gh variable delete '
                             f'{PULL_REQUESTS} --repo {repository}, then install the App: {install}')
    print(f'Stored the App {app["slug"]} in {repository}: variable {CLIENT_ID} and secret {PRIVATE_KEY}. '
          'The private key was not written to disk.\n'
          f'Last step, in your browser: install the App on {owner}, choose "Only select repositories", and select '
          f'exactly {", ".join(names)}. If the page does not open, visit {install}', flush=True)
    webbrowser.open(install)
    print('After you install it, the next check of main reads these sources. ' + (
        'Pull requests from branches of this repository are checked fully too.' if pull_requests else
        'Pull request checks stop at "Materialize exact sources"; the check of main after a merge is the one that counts.'))


if __name__ == '__main__':
    sys.exit('Run ./bilibi connect-private-sources instead.')
