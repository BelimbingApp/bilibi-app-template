"""What this application copy is and what it is doing, read without changing anything.

./bilibi status --json, the previews and the app-maker MCP server all read the
application through state() here, so they describe the same copy the same way.
Nothing in this file clones, fetches, installs, starts or stops anything, and
nothing here returns a secret: use sanitize() for any text that came from a
log, a tool or a component before it is shown or returned.
"""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

OFFICIAL = re.compile(r'https://github\.com/BelimbingProject/[A-Za-z0-9][A-Za-z0-9_.-]*(?:\.git)?')
HOST = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)*\.localhost')
DEFAULT_HOST = 'bilibi.localhost'
# What the Platform's development setup checks for, apart from Node, which the
# maker selects privately. A background start refuses to install any of these.
PREREQUISITES = ('git', 'bash', 'python3', 'curl', 'ss', 'flock', 'psql', 'pg_isready',
                 'rustup', 'cargo', 'rustc', 'cargo-watch')
SECRET_NAME = re.compile(r'PASSWORD|PASSWD|SECRET|TOKEN|CREDENTIAL|AUTHORIZATION|COOKIE|(?:^|_)KEY(?:_ID)?$')
SECRET_WORD = (r'[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|'
               r'authorization|credential)[A-Za-z0-9_.-]*')


def sanitize(text):
    """Text that is safe to show or return: no credentials, tokens, private keys or terminal controls.

    It errs towards removing too much. The caller still treats the result as
    untrusted content, because a log or a component can say anything."""
    text = str(text)
    text = re.sub(r'\x1b\][^\x07]*?(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text.replace('\r\n', '\n').replace('\r', '\n'))
    for key, value in os.environ.items():
        if value and (key.endswith('DATABASE_URL') or len(value) >= 8 and SECRET_NAME.search(key.upper())):
            text = text.replace(value, '[redacted]')
    text = re.sub(r'\A(?:(?!-----BEGIN ).)*?-----END [A-Z ]*PRIVATE KEY-----', '[redacted private key]', text, flags=re.S)
    text = re.sub(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)',
                  '[redacted private key]', text, flags=re.S)
    text = re.sub(r'(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@', r'\1[redacted]@', text, flags=re.I)
    text = re.sub(r'(\b(?:Bearer|Basic)[ \t]+)\S+', r'\1[redacted]', text, flags=re.I)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})', '[redacted]', text)
    text = re.sub(rf'(?i)(\b{SECRET_WORD}["\']?[ \t]*[:=][ \t]*)(?!\[redacted\])\S+', r'\1[redacted]', text)
    return text


def tail(text, lines=200, size=16 * 1024):
    """The end of a log, sanitized and bounded in both lines and bytes."""
    text = sanitize(text)
    kept = '\n'.join(text.splitlines()[-lines:])
    return kept[-size:]


def tail_file(path, lines=200, size=16 * 1024):
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(stream.tell() - 4 * size, 0))
            return tail(stream.read().decode('utf-8', errors='replace'), lines, size)
    except OSError:
        return ''


def git(folder, *args):
    """A read-only Git answer, or None when Git cannot give one."""
    try:
        result = subprocess.run(['git', '-C', str(folder), *args], text=True, capture_output=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def env_setting(path, name):
    """One launcher setting from .env. The rest of the file is never read out."""
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    found = None
    for line in lines:
        key, separator, value = line.strip().removeprefix('export ').lstrip().partition('=')
        if separator and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            found = value
    return found


def address(root, platform):
    """This copy's local address, by the launcher's own rule: shell, then .env, then bilibi.localhost."""
    env_file = Path(root) / '.env'
    host, set_by = os.environ.get('BILIBI_DEV_HOST'), 'the shell'
    if not host:
        host, set_by = env_setting(env_file, 'BILIBI_DEV_HOST'), str(env_file)
    if not host:
        host, set_by = DEFAULT_HOST, 'the default'
    result = {'host': host, 'url': f'https://{host}', 'set_by': set_by, 'env_file': str(env_file),
              'rename': f'Put BILIBI_DEV_HOST=<name>.localhost in {env_file}, then start again.'}
    if not HOST.fullmatch(host):
        result['problem'] = 'BILIBI_DEV_HOST must be a name under .localhost, such as my-bilibi.localhost.'
        return result
    try:
        instance = (Path(platform) / '.local/https/instance').read_text().split()
        if instance[0] == host and instance[2] != '443':
            result['url'] += f':{instance[2]}'
    except (OSError, IndexError):
        pass
    return result


def process_start(pid):
    """The kernel's start time of a process: with the PID, it names one process and never a later one."""
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        # A process that has exited but was not collected yet is no longer running.
        return None if fields[0] in 'ZX' else int(fields[19])
    except (OSError, ValueError, IndexError, TypeError):
        return None


def alive(record):
    """True only for the same process a record names: a reused PID does not count."""
    if not isinstance(record, dict) or not record.get('pid') or record.get('start') is None:
        return False
    return process_start(record['pid']) == record['start']


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    pending.write_text(json.dumps(value, indent=1) + '\n')
    pending.replace(path)


def record_run(root):
    """Remember that this process is this copy's run, so ./bilibi stop can stop exactly it.

    ./bilibi start keeps its process ID through the hand-off to the Platform
    launcher, so the ID recorded here is the launcher that owns the app."""
    try:
        write_json(Path(root) / '.local/run.json', {
            'pid': os.getpid(), 'start': process_start(os.getpid()), 'started_at': time.time(),
            'job': os.environ.get('BILIBI_JOB_ID') or None})
    except OSError:
        pass  # A read-only .local only costs ./bilibi stop; start still works.


def launcher_app_running(platform):
    """Ask this copy's Platform launcher whether its app is up. None: no launcher is running."""
    try:
        folder = os.open(Path(platform) / '.local/dev', os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        # Through the open folder: a deep checkout path exceeds the socket path limit.
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(2)
            connection.connect(f'/proc/self/fd/{folder}/control.sock')
            connection.sendall(b'running\n')
            return connection.recv(32).strip() == b'0'
    except (OSError, ValueError):
        return None
    finally:
        os.close(folder)


def run_state(root, platform):
    """Whether this copy is running, and how far a start has got.

    state is stopped, starting, running or failed. failed means the launcher is
    still up and waiting for a fix, as ./bilibi start does at a terminal."""
    record = read_json(Path(root) / '.local/run.json')
    owned = alive(record)
    app = launcher_app_running(platform)
    result = {'state': 'stopped', 'ready': False, 'pid': record['pid'] if owned else None,
              'job': record.get('job') if owned else None,
              'started_at': record.get('started_at') if owned else None}
    if app is None and not owned:
        return result
    if app is None:
        return {**result, 'state': 'starting', 'phase': 'prepare',
                'detail': 'Fetching sources and checking tools before the launcher starts.'}
    progress = read_json(Path(platform) / '.local/dev-run.status.json') or {}
    result['phase'] = progress.get('phase', 'prepare')
    output = tail(progress.get('output') or '', lines=60, size=6000)
    if progress.get('failed'):
        result.update(state='failed', output=output,
                      detail='The launcher is still running and rebuilds when a file changes; fix the error shown, '
                             'or stop and start again.')
    elif app and result['phase'] == 'serve':
        result.update(state='running', ready=True)
    else:
        result.update(state='starting', output=output)
    if not owned:
        result['detail'] = ('This run was not started by a ./bilibi start that recorded it, so ./bilibi stop cannot '
                            'stop it; press Ctrl-C in the terminal that runs it.')
    return result


def missing_prerequisites():
    return [tool for tool in PREREQUISITES if not shutil.which(tool)]


def wired(platform, layer, identity):
    """True when the Platform's generated module list includes this component."""
    try:
        text = (Path(platform) / 'apps' / layer / 'Cargo.toml').read_text()
    except OSError:
        return False
    return bool(re.search(rf'path\s*=\s*"{re.escape(identity)}/', text))


def component(name, folder, saved, platform):
    """One part of the application: mounted, wired and saved are reported separately."""
    kind = 'platform' if name == 'platform' else name.split('/')[1]
    identity = 'platform' if name == 'platform' else name.rsplit('/', 1)[-1]
    entry = saved.get(name)
    result = {'id': identity, 'kind': kind, 'path': str(folder), 'mounted': False, 'wired': False,
              'saved': entry is not None, 'saved_commit': entry['commit'] if entry else None,
              'saved_url': entry['url'] if entry else None}
    top = git(folder, 'rev-parse', '--show-toplevel') if Path(folder).is_dir() and not Path(folder).is_symlink() else None
    if top is None or Path(top).resolve() != Path(folder).resolve():
        result['problem'] = ('Missing; run ./bilibi setup in a terminal.' if not Path(folder).exists()
                             else 'Not its own Git checkout; move it aside deliberately.')
        return result
    changes = (git(folder, 'status', '--porcelain', '--untracked-files=all') or '').splitlines()
    origin = git(folder, 'remote', 'get-url', 'origin') or ''
    result.update(mounted=True, commit=git(folder, 'rev-parse', 'HEAD'),
                  branch=git(folder, 'branch', '--show-current') or None,
                  uncommitted_files=len(changes), origin=sanitize(origin),
                  source='official' if OFFICIAL.fullmatch(origin) else 'private')
    result['wired'] = True if kind == 'platform' else wired(platform, kind, identity)
    result['matches_saved'] = bool(entry) and result['commit'] == entry['commit'] and origin == entry['url']
    return result


def state(root, platform, repositories, saved_sources):
    """The whole copy: application, address, run and every component.

    repositories yields (name, folder) for what is mounted; saved_sources
    returns the saved record's (name, source) pairs or raises ValueError."""
    root, platform = Path(root), Path(platform)
    result = {'application': {
        'path': str(root), 'branch': git(root, 'branch', '--show-current') or None,
        'commit': git(root, 'rev-parse', 'HEAD'),
        'uncommitted_files': len((git(root, 'status', '--porcelain', '--untracked-files=all') or '').splitlines()),
        'origin': sanitize(git(root, 'remote', 'get-url', 'origin') or '') or None,
        'adopted_maker_tooling': (root / 'maker.lock').is_file()}}
    try:
        saved = dict(saved_sources())
    except (OSError, ValueError, KeyError) as error:
        saved = {}
        result['record_problem'] = sanitize(f'The saved versions cannot be read ({error}); restore setup.toml '
                                            'and composition/ together from Git.')
    result['address'] = address(root, platform)
    result['run'] = run_state(root, platform)
    mounted = dict(repositories())
    names = list(mounted) + sorted(saved.keys() - mounted.keys())
    parts = [component(name, mounted.get(name) or (platform if name == 'platform' else platform / name), saved, platform)
             for name in names]
    result['components'] = parts
    result['unsaved'] = [part['id'] for part in parts if part['mounted'] and not part['saved']]
    result['differs_from_saved'] = [part['id'] for part in parts
                                    if part['mounted'] and part['saved'] and not part['matches_saved']]
    result['missing'] = [part['id'] for part in parts if not part['mounted']]
    steps = []
    if result['missing']:
        steps.append('Sources are missing here: run ./bilibi setup in a terminal (it may ask for your sudo password), '
                     'or start the application, which fetches them.')
    if result['unsaved']:
        steps.append(f'{", ".join(result["unsaved"])}: added here but not saved, so a release would leave it out. '
                     'Commit and push its work, then pin (./bilibi pin) to save it, and check before a release.')
    if result['differs_from_saved']:
        steps.append(f'{", ".join(result["differs_from_saved"])}: differs from the saved version. Pin or check to save '
                     'the new commits, or update to return to the saved ones.')
    if any(part['mounted'] and not part['wired'] for part in parts):
        steps.append('A mounted component is not wired into the local build yet; starting the application wires it.')
    result['next_steps'] = steps
    return result


def version(text):
    try:
        return tuple(int(piece) for piece in text.strip().split('.'))
    except ValueError:
        return None


def satisfies(platform_version, requirement):
    """Whether a Platform version meets a catalogue requirement such as ">=0.1.0, <0.2.0". None: cannot tell."""
    have = version(platform_version or '')
    if have is None or not requirement:
        return None
    tests = {'>=': lambda a, b: a >= b, '<=': lambda a, b: a <= b, '>': lambda a, b: a > b,
             '<': lambda a, b: a < b, '==': lambda a, b: a == b, '=': lambda a, b: a == b}
    for clause in requirement.split(','):
        match = re.fullmatch(r'\s*(>=|<=|==|>|<|=)\s*([0-9.]+)\s*', clause)
        if not match or version(match[2]) is None:
            return None
        if not tests[match[1]](have, version(match[2])):
            return False
    return True


def catalogue_entries(listed, state, platform_version, checked):
    """The official Domains and Extensions, with what is known about each one working here.

    Three kinds of evidence are kept apart and none is stretched into another:
    a release that declares it supports this Platform version, a pair the
    Platform's CI tested at these exact commits, and this application's whole
    saved set passing ./bilibi check on this computer. Without any of them the
    answer is "unknown", never "compatible"."""
    parts = {(part['kind'], part['id']): part for part in state['components']}
    platform = parts.get(('platform', 'platform')) or {}
    entries = []
    for layer in ('domains', 'extensions'):
        for identity, facts in (listed.get(layer) or {}).items():
            part = parts.get((layer, identity)) or {}
            releases = [{'version': number, **{key: release.get(key) for key in ('commit', 'platform', 'released')}}
                        for number, release in (facts.get('versions') or {}).items()]
            entry = {'id': identity, 'kind': layer, 'name': facts.get('name') or identity,
                     'description': sanitize(' '.join(str(facts.get('description') or '').split())),
                     'repository': facts.get('repository'), 'added': bool(part.get('mounted')),
                     'saved': bool(part.get('saved')), 'releases': releases}
            if part.get('mounted'):
                release = next((item for item in releases if item['commit'] == part['commit']), None)
                tested = next((item for item in listed.get('tested') or []
                               if (item.get('domain') or item.get('extension') or {}).get('id') == identity
                               and (item.get('domain') or item.get('extension') or {}).get('commit') == part['commit']
                               and (item.get('platform') or {}).get('commit') == platform.get('commit')), None)
                supported = satisfies(platform_version, release['platform']) if release else None
                whole = bool(checked and part.get('matches_saved') and platform.get('matches_saved'))
                entry['evidence'] = {
                    'declared_version_support': (
                        {'release': release['version'], 'requires_platform': release['platform'],
                         'this_platform': platform_version, 'supported': supported} if release else
                        {'supported': None, 'detail': 'The mounted commit is not a listed release, so no release declares support for it.'}),
                    'exact_pair_tested_by_platform_ci': (
                        {'tested': True, 'tested_at': tested.get('tested_at'), 'run': tested.get('run')} if tested else {'tested': False}),
                    'whole_application_checked_here': whole,
                    'summary': ('This application\'s saved set, including this commit, passed ./bilibi check on this computer.' if whole else
                                'The Platform\'s CI tested exactly this pair of commits.' if tested else
                                'Its release declares support for this Platform version; the pair itself is not recorded as tested.' if supported else
                                'Its release declares that it does not support this Platform version.' if supported is False else
                                'Unknown: nothing records that this combination works. Run ./bilibi check to find out.')}
            entries.append(entry)
    return entries
