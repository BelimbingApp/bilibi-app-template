#!/usr/bin/env python3
"""Create a verified draft, then let the owner publish it immutably."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(os.environ.get('BILIBI_TOOL_APPLICATION_ROOT', Path(__file__).resolve().parent.parent)).resolve()


def gh(*args):
    return subprocess.run(['gh-axi', *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def api(path):
    # gh-axi renders TOON. Asking jq for base64 keeps the JSON body on one line
    # without making callers parse arbitrary TOON tables or escaped strings.
    result = gh('api', path, '--jq', '@base64', '--full')
    for line in result.splitlines():
        if line.startswith('  body: '):
            return json.loads(base64.b64decode(line[8:].strip('"')))
    raise ValueError(f'No JSON response from gh-axi: {result}')


def find_release(repository, tag):
    # The by-tag endpoint hides drafts, so read the authenticated release list.
    matches = []
    page = 1
    while True:
        batch = api(f'/repos/{repository}/releases?per_page=100&page={page}')
        matches += [release for release in batch if release['tag_name'] == tag]
        if len(batch) < 100:
            break
        page += 1
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one GitHub release for {tag}, found {len(matches)}')
    return matches[0]


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_files(folder):
    sums = folder / 'SHA256SUMS'
    expected = {}
    for line in sums.read_text().splitlines():
        sha, name = line.split('  ', 1)
        if not re.fullmatch('[0-9a-f]{64}', sha) or Path(name).name != name or name in expected:
            raise ValueError('Invalid release checksum list')
        expected[name] = sha
        if checksum(folder / name) != sha:
            raise ValueError(f'Checksum mismatch: {name}')
    expected['SHA256SUMS'] = checksum(sums)
    if {p.name for p in folder.iterdir()} != set(expected):
        raise ValueError('Unexpected or missing release assets')
    return expected


def verify_assets(release, expected):
    assets = release['assets']
    if len(assets) != len(expected) or {a['name'] for a in assets} != set(expected):
        raise ValueError('GitHub release has unexpected or missing assets')
    for asset in assets:
        if asset.get('digest') != 'sha256:' + expected[asset['name']]:
            raise ValueError(f'GitHub asset digest mismatch: {asset["name"]}')


def validate_tag(tag):
    if not re.fullmatch(r'setup-v\d+\.\d+\.\d+(?:-test)?', tag):
        raise ValueError('Use setup-vX.Y.Z or setup-vX.Y.Z-test for the release tag.')


def check_new_tag(tag, root):
    validate_tag(tag)
    ref = f'refs/tags/{tag}'
    if subprocess.run(['git', 'show-ref', '--verify', '--quiet', ref], cwd=root).returncode == 0:
        raise ValueError(f'{tag} already exists here. Choose a new release version.')
    result = subprocess.run(['git', 'ls-remote', '--exit-code', 'origin', ref], cwd=root,
                            text=True, stdout=subprocess.PIPE, check=False)
    if result.returncode == 0:
        raise ValueError(f'{tag} already exists on origin. Choose a new release version.')
    if result.returncode != 2:
        raise ValueError('Cannot check release tags on origin. Check your connection and Git access, then retry.')


def require_default_branch(root):
    remote = subprocess.run(['git', 'ls-remote', '--symref', 'origin', 'HEAD'], cwd=root,
                            text=True, stdout=subprocess.PIPE, check=False)
    match = re.search(r'^ref: refs/heads/([^\s]+)\s+HEAD$', remote.stdout, re.MULTILINE)
    branch = match.group(1) if match else None
    if remote.returncode == 0 and branch is None:
        upstream = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'],
                                  cwd=root, text=True, stdout=subprocess.PIPE, check=False)
        if upstream.returncode == 0 and upstream.stdout.strip().startswith('origin/'):
            branch = upstream.stdout.strip().removeprefix('origin/')
    if remote.returncode or branch is None:
        raise ValueError('Cannot read the default branch from origin. Check your connection and Git access, then retry.')
    fetched = subprocess.run(['git', 'fetch', '--quiet', '--no-tags', 'origin',
                              f'+refs/heads/{branch}:refs/remotes/origin/{branch}'], cwd=root)
    if fetched.returncode:
        raise ValueError(f'Cannot read origin/{branch}. Check your connection and Git access, then retry.')
    reviewed = subprocess.run(['git', 'merge-base', '--is-ancestor', 'HEAD', f'origin/{branch}'], cwd=root)
    if reviewed.returncode:
        raise ValueError(f'Release only a reviewed commit on the default branch ({branch}). Merge and push it first.')


def start_release(tag, root):
    require_default_branch(root)
    subprocess.run(['git', 'tag', tag], cwd=root, check=True)
    if subprocess.run(['git', 'push', 'origin', f'refs/tags/{tag}'], cwd=root).returncode != 0:
        subprocess.run(['git', 'tag', '-d', tag], cwd=root, check=True)
        raise ValueError(f'{tag} was not published to origin. Check your connection and Git access, '
                         'then run the release command again.')
    print(f'Started the GitHub release build for {tag}. Follow Release setup in GitHub Actions. '
          'Publish the verified draft using docs/guides/releasing.md.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('draft', 'publish'))
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repository', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        parser.error('Use owner/name for the repository')
    validate_tag(args.tag)
    expected = verify_files(ROOT / 'dist')
    record = json.loads((ROOT / 'dist/recipe.json').read_text())
    setup = record['setup']
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    tag_commit = subprocess.check_output(['git', 'rev-list', '-1', args.tag], cwd=ROOT, text=True).strip()
    if any(setup.get(key) != value for key, value in
           {'repository': args.repository, 'tag': args.tag, 'commit': commit}.items()) or tag_commit != commit:
        raise ValueError('Release recipe, checkout and tag must identify the same setup commit')
    if api(f'/repos/{args.repository}/commits/{args.tag}')['sha'] != commit:
        raise ValueError('The remote tag names a different recipe commit')
    target = record['built_for']['target']
    names = {f'bilibi-{args.tag}-{target}.tar.gz', f'bilibi-installer-{target}',
             'bilibi-server-tools.tar.gz', 'recipe.json', 'builder-packages.txt',
             'publication.json', 'SHA256SUMS'}
    if set(expected) != names:
        raise ValueError('The release does not contain the complete asset set')
    if args.command == 'draft':
        # No --clobber and no reuse: a rerun must not replace an existing asset.
        print(gh('release', 'create', args.tag, '--repo', args.repository, '--verify-tag', '--draft',
                 '--title', args.tag, '--notes',
                 f'Built from {commit}. Verify SHA256SUMS and recipe.json. Install the bilibi-setup archive through Software upload. '
                 'The installer and server tools are separate bootstrap assets. Checksums are not signatures.',
                 *map(str, sorted((ROOT / 'dist').iterdir()))))
    release = find_release(args.repository, args.tag)
    if not release['draft']:
        raise ValueError('This release is already published; never replace its tag or assets')
    verify_assets(release, expected)
    if args.command == 'publish':
        if args.tag.endswith('-test'):
            raise ValueError('Test releases stay drafts so their tag and release can be deleted')
        # This runs with the owner's gh-axi session, never a build/publish secret.
        if not api(f'/repos/{args.repository}/immutable-releases')['enabled']:
            raise ValueError('Enable release immutability in repository Settings before publishing')
        print(gh('release', 'edit', args.tag, '--repo', args.repository, '--draft=false'))
        published = find_release(args.repository, args.tag)
        if published['draft'] or not published.get('immutable'):
            raise ValueError('GitHub did not report an immutable published release')
        verify_assets(published, expected)
    print(f'Verified {len(expected)} asset digests for {release["html_url"]}')


if __name__ == '__main__':
    main()
