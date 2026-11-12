"""Choose the Platform release ./bilibi upgrade moves to, and collect its upgrade notes.

Everything here is read as data from Git: the Platform's published catalogue
(its `catalogue` branch) and the notes files in the Platform release commits.
Nothing fetched is run.
"""
import copy
import json
import re
import subprocess

OFFICIAL = re.compile(r'https://github\.com/BelimbingProject/[A-Za-z0-9][A-Za-z0-9_.-]*\.git')
COMPARATOR = re.compile(r'(>=|<=|>|<|=)\s*(\d+\.\d+\.\d+)')
# GitHub refuses pull request bodies over 65536 characters.
NOTES_LIMIT = 50000


def version_tuple(text):
    return tuple(int(part) for part in text.split('.'))


def satisfies(version, requirement):
    """True when version meets every comparator of a requirement such as ">=0.2.0, <0.3.0"."""
    actual = version_tuple(version)
    checks = {'>=': actual.__ge__, '<=': actual.__le__, '>': actual.__gt__, '<': actual.__lt__, '=': actual.__eq__}
    for piece in requirement.split(','):
        match = COMPARATOR.fullmatch(piece.strip())
        if not match or not checks[match[1]](version_tuple(match[2])):
            return False
    return True


def same_repository(catalogued, url):
    return catalogued.removesuffix('.git') + '.git' == url


class Objects:
    """A scratch Git repository holding only what the upgrade fetched; never a checkout."""

    def __init__(self, folder):
        self.folder = folder
        self.git('init', '--quiet', '--bare')

    def git(self, *args, check=True):
        result = subprocess.run(['git', '-C', self.folder, *args], capture_output=True, text=True)
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
        return result

    def fetch(self, url, *revisions):
        # Blobs arrive only when a notes file is read, where the server supports filters.
        self.git('-c', 'core.hooksPath=/dev/null', 'fetch', '--quiet', '--no-tags', '--filter=blob:none',
                 url, *revisions)

    def has(self, commit):
        return self.git('cat-file', '-e', f'{commit}^{{commit}}', check=False).returncode == 0

    def contains(self, newer, older):
        """True when older is newer itself or in its history."""
        return self.has(older) and self.git('merge-base', '--is-ancestor', older, newer, check=False).returncode == 0

    def show(self, revision, path):
        result = self.git('show', f'{revision}:{path}', check=False)
        return result.stdout if result.returncode == 0 else None


def catalogue(objects, url):
    """The Platform's published catalogue: its releases, its official components and their tested sets."""
    try:
        objects.fetch(url, '+refs/heads/catalogue:refs/catalogue')
    except subprocess.CalledProcessError as error:
        raise ValueError(f'cannot read the Platform catalogue from {url} ({error.stderr.strip() or error}); '
                         'check your connection to GitHub, then rerun ./bilibi upgrade') from error
    try:
        data = json.loads(objects.show('refs/catalogue', 'catalogue.json') or '')
        releases = data['platform']['versions']
        if not same_repository(data['platform']['repository'], url) or not isinstance(releases, dict):
            raise ValueError('it names another Platform repository')
        for version, release in releases.items():
            version_tuple(version)
            release['commit']
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f'the Platform catalogue at {url} cannot be read ({error}); nothing was changed. '
                         'Report it to the Bilibi team, then rerun ./bilibi upgrade') from error
    return data


def recommended(facts, platform_version, platform_commit, tested):
    """A component's newest release that supports the Platform release, preferring a tested pair."""
    choices = []
    for version, release in facts.get('versions', {}).items():
        if satisfies(platform_version, release.get('platform', '')):
            checked = any(item['platform']['commit'] == platform_commit and item['platform']['version'] == platform_version
                          and item['domain'] == {'id': facts['id'], 'version': version, 'commit': release['commit']}
                          for item in tested)
            choices.append((checked, version_tuple(version), version, release['commit']))
    return max(choices)[2:] if choices else None


def plan(setup, objects):
    """Return (new setup, lines to show, release), or (None, [why the Platform stays], None)."""
    platform = setup['platform']
    url, current = platform['url'], platform['commit']
    if not OFFICIAL.fullmatch(url):
        return None, [f'The Platform comes from {url}, not an official BelimbingProject repository, so it stays at its saved commit.'], None
    data = catalogue(objects, url)
    releases = data['platform']['versions']
    if not releases:
        return None, ['The Platform has no release yet, so it stays at its saved commit.'], None
    version = max(releases, key=version_tuple)
    target = releases[version]['commit']
    try:
        objects.fetch(url, target, current)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'cannot fetch Platform commits {target[:12]} and {current[:12]} from {url}; check your '
                         'connection to GitHub, then rerun ./bilibi upgrade') from error
    if current == target:
        return None, [f'The Platform is already at its newest release, v{version}.'], None
    if objects.contains(current, target):
        return None, [f'The Platform is already past its newest release, v{version}: its saved commit {current[:12]} '
                      'includes it. Upgrade never moves it back.'], None
    if not objects.contains(target, current):
        return None, [f'The Platform stays at {current[:12]}: that commit is not in the history of release v{version}. '
                      'Choose its version deliberately with ./bilibi check.'], None
    moved = copy.deepcopy(setup)
    moved['platform']['commit'] = target
    lines = [f'Platform -> v{version} ({target[:12]})']
    for layer in ('domains', 'extensions'):
        for source in moved.get(layer, []):
            name = source['id']
            facts = data.get(layer, {}).get(name)
            if not facts or not same_repository(facts['repository'], source['url']) or not OFFICIAL.fullmatch(source['url']):
                lines.append(f'{name}: not an official component, so it stays at {source["commit"][:12]}; '
                             'its owner updates it using the upgrade notes below')
                continue
            choice = recommended({**facts, 'id': name}, version, target, data.get('tested', []) if layer == 'domains' else [])
            if choice is None:
                return None, [f'The Platform stays at {current[:12]}: {name} has no release for Platform v{version} yet. '
                              f'Ask its owner to publish one, then rerun ./bilibi upgrade.'], None
            release, commit = choice
            if commit == source['commit']:
                lines.append(f'{name}: already at {release}')
                continue
            try:
                objects.fetch(source['url'], commit, source['commit'])
            except subprocess.CalledProcessError as error:
                raise ValueError(f'cannot fetch {name} commits {commit[:12]} and {source["commit"][:12]} from '
                                 f'{source["url"]}; check your access, then rerun ./bilibi upgrade') from error
            if objects.contains(source['commit'], commit):
                lines.append(f'{name}: stays at {source["commit"][:12]}, which already includes {release}')
            elif objects.contains(commit, source['commit']):
                lines.append(f'{name} -> {release} ({commit[:12]})')
                source['commit'] = commit
            else:
                return None, [f'The Platform stays at {current[:12]}: {name} is saved at {source["commit"][:12]}, which is '
                              f'not in the history of its release {release} for Platform v{version}. '
                              'Choose versions deliberately with ./bilibi check.'], None
    return moved, lines, f'v{version}'


def crossed(objects, url, old, new):
    """[(version, notes)] for each Platform release new includes and old does not, oldest first."""
    data = catalogue(objects, url)
    objects.fetch(url, new, old)
    found = []
    for version, release in sorted(data['platform']['versions'].items(), key=lambda item: version_tuple(item[0])):
        if objects.contains(new, release['commit']) and not objects.contains(old, release['commit']):
            notes = objects.show(new, f'docs/upgrade-notes/v{version}.md')
            found.append((version, notes.strip() if notes else
                          f'This release has no upgrade notes file. See {url.removesuffix(".git")}/releases/tag/v{version}'))
    return found


def report(old_setup, new_setup, objects, lines=None, limit=NOTES_LIMIT):
    """The Platform part of the pull request: what moved and the upgrade notes for agents it crossed."""
    url = new_setup['platform']['url']
    notes = crossed(objects, url, old_setup['platform']['commit'], new_setup['platform']['commit'])
    if lines is None:
        old = {source['id']: source['commit'] for layer in ('domains', 'extensions') for source in old_setup.get(layer, [])}
        lines = [f'Platform -> {", ".join("v" + version for version, _ in notes) or new_setup["platform"]["commit"][:12]}']
        lines += [f'{source["id"]} -> {source["commit"][:12]}' for layer in ('domains', 'extensions')
                  for source in new_setup.get(layer, []) if old.get(source['id']) != source['commit']]
    text = 'Moves the Platform to its newest recommended release, with the official Domains and Extensions:\n\n'
    text += ''.join(f'- {line}\n' for line in lines)
    text += ('\nThe saved build record (setup.toml and composition/) is pinned to these versions. CI builds and tests them '
             'on this pull request.\n\n## Upgrade notes for agents\n\n'
             'What changed in each Platform release this upgrade crosses. Use them to update your own Domains and '
             'Extensions before merging.\n')
    used = 0
    for version, body in notes:
        if limit is not None and used + len(body) > limit:
            body = f'Too long to include here. Read them at {url.removesuffix(".git")}/releases/tag/v{version}'
        used += len(body)
        # Drop the notes' own title, and keep their headings below this one.
        body = re.sub(r'\A# [^\n]*\n+', '', body)
        body = re.sub(r'^(#+) ', lambda match: '#' * min(len(match[1]) + 2, 6) + ' ', body, flags=re.M)
        text += f'\n### Platform v{version}\n\n{body}\n'
    return text
