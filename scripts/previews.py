"""Say exactly what a command would do, before it does it, without changing anything.

./bilibi <command> --preview prints a preview, and the app-maker MCP server
shows the same one to the operator before it applies a change. A preview names
the application, the exact actions and their effects, and the plain command
that carries them out. Its fingerprint covers the state it was computed from,
so an approval given for one preview cannot be used after something changed.

Add a new previewed operation here, as one function that returns its plan;
do not describe a command a second time in the MCP server.
"""
import hashlib
import json
import os
import re
import time
from urllib.parse import urlsplit

import app_state

COMPONENT = re.compile(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*')
# Each operation with what the operator may give it.
OPERATIONS = {
    'start': (), 'stop': (), 'add': ('component', 'kind', 'source', 'commit'), 'update': (), 'pin': (),
    'check': (), 'release': ('tag',), 'publish': ('tag',), 'upgrade': (),
}
PRIVATE_NOTE = ('GitHub checks cannot read a private source until you run ./bilibi connect-private-sources once in a '
                'terminal (docs/guides/private-sources.md).')


def source_problem(url):
    """Why a source URL is refused, or None. Credentials never belong in a URL."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or port):
        return ('--source must be an HTTPS Git URL without a user name, password, token, port, query or fragment, '
                'such as https://github.com/your-company/pricing. Your existing Git sign-in supplies the access.')
    return None


def moved(state):
    return [part for part in state['components'] if part['mounted'] and not part.get('matches_saved')]


def moves(state):
    return [f'{part["id"]}: {(part["saved_commit"] or "")[:7] or "not included"} -> {part["commit"][:7]} '
            f'from {part["origin"]}' for part in moved(state)]


def private(state):
    return [part['id'] for part in state['components'] if part.get('source') == 'private']


def uncommitted(state, platform=False):
    return [part['id'] for part in state['components']
            if part['mounted'] and part['uncommitted_files'] and (platform or part['kind'] != 'platform')]


def start(ws, state, _arguments):
    run, url = state['run'], state['address']['url']
    if run['state'] != 'stopped':
        return {'nothing': f'This copy is already {run["state"]} at {url}; a second start would be refused.'}
    actions = []
    if state['missing']:
        actions.append(f'Fetch the missing sources at their saved commits: {", ".join(state["missing"])}.')
    actions += ['Run ./bilibi start in the background; its output goes to a job log under .local/jobs.',
                f'Build the local sources and serve the application at {url}. The first build takes a while.']
    plan = {'command': ['start'], 'kind': 'run', 'actions': actions, 'effects': [
        'Your branches, edits and data are kept. It runs local sources, not the saved release versions.',
        'It installs no system packages and never asks for sudo. It may download the pinned private Node.js into build/.',
        'A first start creates this copy\'s own PostgreSQL 18 data and development keys under sources/platform/.local.',
        'If another copy owns the address, the start stops and its log gives the exact BILIBI_DEV_HOST line for .env.']}
    missing = app_state.missing_prerequisites()
    if missing:
        plan['blocked'] = (f'Missing on this computer: {", ".join(missing)}. A background start never installs them, '
                           'because installing may ask for your sudo password. Run ./bilibi setup in a terminal, '
                           'then start again.')
    return plan


def stop(ws, state, _arguments):
    run, url = state['run'], state['address']['url']
    if run['state'] == 'stopped':
        return {'nothing': 'This copy is not running.'}
    if not run.get('pid'):
        return {'actions': [], 'effects': [], 'command': ['stop', '--yes'], 'blocked': run.get('detail') or
                'This run cannot be identified, so nothing is stopped.'}
    since = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(run['started_at'])) if run.get('started_at') else 'unknown'
    return {'command': ['stop', '--yes'], 'actions': [
        f'Ask launcher process {run["pid"]} of this copy (started {since}) to stop, and wait until it has exited.'],
        'effects': [f'{url} stops answering.',
                    'The database, keys, uploaded files and sources are kept. This copy\'s private PostgreSQL keeps running.',
                    'No other copy on this computer is touched, and nothing is stopped by name.']}


def add(ws, state, arguments):
    component = arguments.get('component') or ''
    explicit = [arguments.get(key) for key in ('kind', 'source', 'commit')]
    command = ['add', component]
    plan = {'command': command, 'actions': [], 'effects': [
        'It is mounted only. It is not wired into the build and not saved for release until the next steps.',
        'Next: start (wires and runs it), then pin to save it, then check before a release.',
        'No existing source, branch or edit is changed.']}
    if not COMPONENT.fullmatch(component) or component in ('base', 'core', 'web', 'src'):
        return {**plan, 'blocked': 'Give a component ID such as finance or supply_chain.'}
    if state['missing'] and 'platform' in state['missing']:
        plan['actions'].append('Fetch the missing Platform sources at their saved commit first.')
    if any(explicit):
        kind, source, commit = explicit
        if not all(explicit) or kind not in ('domains', 'extensions'):
            return {**plan, 'blocked': 'kind (domains or extensions), source and commit go together; give all three or none.'}
        if not re.fullmatch('[0-9a-f]{40}', commit):
            return {**plan, 'blocked': 'commit must be a full 40-character lowercase commit ID.'}
        if source_problem(source):
            return {**plan, 'blocked': source_problem(source)}
        command += ['--kind', kind, '--source', source, '--commit', commit]
        folder = ws.PLATFORM / 'apps' / kind / component
        if folder.exists():
            plan['actions'].append(f'Keep the existing checkout at {folder}, after checking its origin and commit.')
        else:
            plan['actions'].append(f'Clone {source} into {folder} and check out commit {commit}.')
        plan['actions'].append(f'Check that its bilibi.container.toml says id "{component}" and the matching kind.')
        if not app_state.OFFICIAL.fullmatch(source):
            plan['effects'] += [
                'This is a private source, not an official BelimbingProject one: its code runs inside your application.',
                'Access uses your existing Git sign-in on this computer. A refused sign-in stops the command; '
                'nobody is asked to type or paste a credential.', PRIVATE_NOTE]
        return plan
    try:
        listed = ws.catalogue()
    except (OSError, ValueError) as error:
        return {**plan, 'blocked': f'The catalogue cannot be read here ({error}). Start once, or run ./bilibi setup in a '
                                   'terminal, to fetch the Platform sources.'}
    found = [(layer, facts) for layer in ('domains', 'extensions')
             if (facts := (listed.get(layer) or {}).get(component)) is not None]
    if len(found) != 1:
        return {**plan, 'blocked': f'{component} is not one official Domain or Extension in the catalogue. For your own '
                                   'component, give kind, source and commit.'}
    layer, facts = found[0]
    folder = ws.PLATFORM / 'apps' / layer / component
    if folder.exists():
        return {'nothing': f'{component} is already added at {folder}.'}
    plan['actions'].append(f'Clone {facts["repository"]} at its default branch into {folder}.')
    return plan


def update(ws, state, _arguments):
    actions = []
    if state['application']['adopted_maker_tooling']:
        actions.append('Move maker.lock to the newest official maker tooling and list its commits; you publish that '
                       'change in a pull request.')
    actions += ['Fetch your application branch and fast-forward it to what your team published.']
    for part in state['components']:
        if part['mounted'] and part['saved'] and not part['matches_saved']:
            actions.append(f'{part["id"]}: check out the saved commit {part["saved_commit"][:7]} (now {part["commit"][:7]}).')
    actions += [f'Fetch missing sources: {", ".join(state["missing"])}.'] if state['missing'] else []
    actions.append('Restore the saved dependency lock and generated module list.')
    effects = ['This restores your team\'s saved versions. It does not upgrade anything to a newer release.',
               'It refuses, and changes nothing, when a checkout has uncommitted edits, untracked files or unpushed '
               'commits, or when a mounted component is not in the saved versions.']
    dirty = uncommitted(state, platform=True) + (['the application'] if state['application']['uncommitted_files'] else [])
    if dirty:
        effects.append(f'Uncommitted files now in: {", ".join(dirty)}. Update will refuse unless they are only '
                       'generated files; commit or stash that work yourself first.')
    if state['unsaved']:
        effects.append(f'Not saved yet: {", ".join(state["unsaved"])}. Update will refuse until it is pinned or moved away.')
    return {'command': ['update'], 'actions': actions, 'effects': effects}


def pin(ws, state, _arguments):
    changes = moves(state)
    if not changes:
        return {'nothing': 'The saved versions already match every mounted source; there is nothing to pin.'}
    plan = {'command': ['pin', '--yes'], 'actions': [f'Save {line}.' for line in changes] + [
        'Fetch exactly those commits from their origins into a separate candidate and resolve its dependencies.',
        'Replace setup.toml, composition/manifest.txt and composition/Cargo.lock together, or none of them.'],
        'effects': ['This replaces the saved versions without building or testing them.',
                    'A release still needs a successful ./bilibi check of this record; release refuses without one.',
                    'Your local branches and edits stay as they are. Commit the three files to share the pin.']}
    if private(state):
        plan['effects'].append(f'Private: {", ".join(private(state))}. {PRIVATE_NOTE}')
    if uncommitted(state):
        plan['blocked'] = (f'{", ".join(uncommitted(state))}: uncommitted files. Commit and push that work in the '
                           'component\'s own repository first; a pin saves only pushed commits.')
    return plan


def check(ws, state, _arguments):
    changes = moves(state)
    actions = [f'Build with and, only if every check passes, save {line}.' for line in changes]
    actions += ['Build the candidate in a separate checkout and test it against the empty database named by '
                'BILIBI_CHECK_DATABASE_URL. This takes a while.',
                'Record the successful check, which ./bilibi release requires.']
    plan = {'command': ['check', '--yes'] if changes else ['check'], 'actions': actions, 'effects': [
        'A failed build or check leaves the saved versions as they were.',
        'It leaves migrations and seed data in that test database; it never uses the development database.']}
    if not os.environ.get('BILIBI_CHECK_DATABASE_URL'):
        plan['blocked'] = ('BILIBI_CHECK_DATABASE_URL is not set where this command runs. Point it at a separate empty '
                           'PostgreSQL 18 database in your shell, or in the environment of the MCP registration '
                           '(docs/guides/releasing.md). Never type a database address or password into a chat.')
    elif uncommitted(state):
        plan['blocked'] = (f'{", ".join(uncommitted(state))}: uncommitted files. Commit and push that work first.')
    return plan


def release(ws, state, arguments):
    tag = arguments.get('tag')
    plan = {'actions': [], 'effects': [
        'Nothing is published by this step. Publishing is the separate operation "publish", with its own confirmation.',
        'It refuses uncommitted files, a record without a successful check, a copy that is not the pushed default '
        'branch, and a default branch whose Check setup has not passed for this commit.']}
    try:
        if tag:
            ws.publish.validate_tag(tag)
        else:
            tag = ws.github_release.suggest(ws.ROOT)[0]
    except ValueError as error:
        return {**plan, 'command': ['release'], 'blocked': str(error)}
    head = (state['application']['commit'] or '')[:7]
    plan['command'] = ['release', '--tag', tag, '--yes', '--draft']
    plan['actions'] = [f'Push tag {tag} for application commit {head} to origin. This starts the Release setup build '
                       'on GitHub, and the version can never be reused.',
                       'Wait for that build, then leave the release as a draft on GitHub.']
    if state['unsaved']:
        plan['blocked'] = ws.unsaved_refusal(state['unsaved'])
    elif state['application']['uncommitted_files']:
        plan['blocked'] = 'The application has uncommitted files. Commit the checked record and merge it into the default branch first.'
    return plan


def publish(ws, state, arguments):
    tag = arguments.get('tag') or ''
    plan = {'command': ['release', '--tag', tag], 'actions': [
        f'Download every asset of the draft release {tag} and verify it against SHA256SUMS and GitHub.',
        f'Publish {tag} on GitHub.'],
        'effects': ['A published release is immutable: it cannot be changed or withdrawn, only followed by a new version.',
                    'Nothing is installed on any server. The log then names the file to upload on each Software page.']}
    try:
        ws.publish.validate_tag(tag)
        if ws.github_release.remote_tag(ws.ROOT, tag) is None:
            plan['blocked'] = f'{tag} is not on origin yet. Run the operation "release" first; it builds the draft.'
    except ValueError as error:
        plan['blocked'] = str(error)
    return plan


def upgrade(ws, state, _arguments):
    actions = ['Read the Platform\'s catalogue and choose the newest recommended release of the Platform together with '
               'the matching release of every official Domain and Extension.',
               'Print the upgrade notes for agents to the job log.',
               'Create a platform-upgrade branch, push it to origin and open one pull request for you to review.']
    if not state['application']['adopted_maker_tooling']:
        actions.insert(2, 'Merge the maker\'s newer files into the same branch.')
    effects = ['Your default branch changes only when you review and merge that pull request.',
               'Nothing newer is claimed to work together beyond what the catalogue and the pull request\'s checks show.']
    if private(state):
        effects.append(f'Private, and not changed by this: {", ".join(private(state))}. Their owners must update them '
                       'from the upgrade notes; until then the pull request\'s check may fail.')
    plan = {'command': ['upgrade'], 'actions': actions, 'effects': effects}
    if state['application']['uncommitted_files']:
        plan['blocked'] = 'The application has uncommitted files. Commit or stash them yourself first.'
    return plan


def fingerprint(ws, state, plan):
    """A digest of everything the preview depends on. Any change makes an earlier preview stale."""
    value = hashlib.sha256()
    for name in (*ws.RECORD_FILES, 'maker.lock'):
        try:
            value.update(name.encode() + b'\0' + (ws.ROOT / name).read_bytes() + b'\0')
        except OSError:
            value.update(name.encode() + b'\0missing\0')
    parts = [(part['id'], part['kind'], part['mounted'], part.get('commit'), part.get('origin'), part.get('branch'),
              app_state.git(part['path'], 'status', '--porcelain', '--untracked-files=all') if part['mounted'] else None)
             for part in state['components']]
    facts = [state['application']['commit'], state['application']['branch'],
             app_state.git(ws.ROOT, 'status', '--porcelain', '--untracked-files=all'),
             state['address']['host'], state['run']['state'], state['run'].get('pid'), parts,
             plan.get('command'), plan.get('actions'), plan.get('blocked'), plan.get('nothing')]
    value.update(json.dumps(facts, sort_keys=True).encode())
    return value.hexdigest()


def preview(ws, operation, arguments=None):
    """The plan for one operation on this copy, as data. Nothing is changed."""
    if operation not in OPERATIONS:
        raise ValueError(f'Unknown operation {operation!r}; choose one of: {", ".join(OPERATIONS)}.')
    arguments = {key: value for key, value in (arguments or {}).items() if value is not None}
    unknown = set(arguments) - set(OPERATIONS[operation])
    if unknown or any(not isinstance(value, str) for value in arguments.values()):
        allowed = ', '.join(OPERATIONS[operation]) or 'none'
        raise ValueError(f'{operation} takes these text arguments only: {allowed}.')
    state = ws.current_state()
    plan = globals()[operation](ws, state, arguments)
    plan = {'operation': operation, 'arguments': arguments,
            'target': {'application': state['application']['path'], 'address': state['address']['url']},
            'kind': 'change', 'actions': [], 'effects': [], **plan}
    plan['fingerprint'] = fingerprint(ws, state, plan)
    return plan


def render(plan):
    """The preview as the text an operator reads before saying yes."""
    arguments = ' '.join(f'{key}={value}' for key, value in plan['arguments'].items())
    lines = [f'Preview of {plan["operation"]}{" " + arguments if arguments else ""}',
             f'Application: {plan["target"]["application"]} ({plan["target"]["address"]})']
    if plan.get('nothing'):
        return '\n'.join(lines + [f'Nothing to do: {plan["nothing"]}'])
    if plan.get('command'):
        lines.append('Command: ./bilibi ' + ' '.join(plan['command']))
    lines += ['What it does:'] + [f'  {number}. {action}' for number, action in enumerate(plan['actions'], 1)]
    lines += ['What that means:'] + [f'  - {effect}' for effect in plan['effects']]
    if plan.get('blocked'):
        lines.append(f'It cannot run yet: {plan["blocked"]}')
    return '\n'.join(lines)
