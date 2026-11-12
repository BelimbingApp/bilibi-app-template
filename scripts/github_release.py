"""The GitHub half of ./bilibi release: tag, wait for the build, publish, download and explain the install.

workspace.release() checks the local record first. Every step here can be
repeated: ./bilibi release --tag <the same tag> continues a release whose tag
is already on origin, from whichever step it stopped at.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

import publish

# How often to ask GitHub about the build, and how long it may take to start one.
POLL_SECONDS = float(os.environ.get('BILIBI_RELEASE_POLL_SECONDS', '30'))
START_LIMIT_SECONDS = 600
VERSION = re.compile(r'refs/tags/setup-v(\d+)\.(\d+)\.(\d+)')


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, text=True, stdout=subprocess.PIPE, check=True).stdout.strip()


def api(path):
    try:
        return publish.api(path)
    except subprocess.CalledProcessError as error:
        raise ValueError('GitHub did not answer through gh-axi (see the message above). Sign in with gh auth login, '
                         'using an account that can read Actions and publish releases in this repository, '
                         'then rerun ./bilibi release.') from error


def github_helper(tools, root):
    """Put the maker's locked gh-axi on PATH, installing it under .local with npm ci when needed."""
    source = tools / 'scripts/github-tools'
    target = root / '.local/github-tools'
    binary = target / 'node_modules/.bin'
    locked = (source / 'package-lock.json').read_bytes()
    installed = target / 'package-lock.json'
    if not (binary / 'gh-axi').exists() or not installed.is_file() or installed.read_bytes() != locked:
        if not shutil.which('npm'):
            raise ValueError('Release needs Node.js 20 or newer with npm for the GitHub helper. Run ./bilibi setup, '
                             'or install Node.js, then rerun ./bilibi release.')
        print('Installing the locked GitHub helper (gh-axi) under .local/github-tools.', flush=True)
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        for name in ('package.json', 'package-lock.json'):
            shutil.copy2(source / name, target / name)
        subprocess.run(['npm', 'ci', '--prefix', str(target), '--ignore-scripts', '--no-audit', '--no-fund'], check=True)
    os.environ['PATH'] = str(binary) + os.pathsep + os.environ.get('PATH', '')


def require_main_check(repository, branch, root):
    runs = api(f'/repos/{repository}/actions/workflows/check.yml/runs?branch={branch}&event=push&per_page=1')['workflow_runs']
    if not runs:
        raise ValueError(f'GitHub has no Check setup run on {branch} yet. Push {branch}, wait for Check setup to pass, '
                         'then rerun ./bilibi release.')
    last = runs[0]
    if last['head_sha'] != git(root, 'rev-parse', f'refs/remotes/origin/{branch}'):
        raise ValueError(f'Check setup has not run on the newest {branch} commit yet. Wait for it to start and pass, '
                         'then rerun ./bilibi release.')
    if last['status'] != 'completed':
        raise ValueError(f'Check setup is still running on {branch}: {last["html_url"]}\n'
                         'Wait for it to pass, then rerun ./bilibi release.')
    if last['conclusion'] != 'success':
        raise ValueError(f'The last Check setup on {branch} did not pass ({last["conclusion"]}): {last["html_url"]}\n'
                         f'Fix {branch} first, then rerun ./bilibi release.')
    print(f'Check setup passed on {branch} (commit {last["head_sha"][:7]}).', flush=True)


def remote_tag(root, tag):
    """The commit a tag names on origin, or None."""
    listing = subprocess.run(['git', 'ls-remote', 'origin', f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}'], cwd=root,
                             text=True, stdout=subprocess.PIPE, check=False)
    if listing.returncode:
        raise ValueError('Cannot read release tags on origin. Check your connection and Git access, then retry.')
    found = dict(reversed(line.split('\t')) for line in listing.stdout.splitlines())
    return found.get(f'refs/tags/{tag}^{{}}') or found.get(f'refs/tags/{tag}')


def suggest(root):
    """The next patch version after the newest release tag on origin, and that tag."""
    listing = subprocess.run(['git', 'ls-remote', '--tags', '--refs', 'origin', 'refs/tags/setup-v*'], cwd=root,
                             text=True, stdout=subprocess.PIPE, check=False)
    if listing.returncode:
        raise ValueError('Cannot read release tags on origin. Check your connection and Git access, then retry.')
    versions = [tuple(map(int, match.groups())) for line in listing.stdout.splitlines()
                if (match := VERSION.fullmatch(line.split('\t')[-1]))]
    if not versions:
        return 'setup-v1.0.0', None
    major, minor, patch = max(versions)
    return f'setup-v{major}.{minor}.{patch + 1}', f'setup-v{major}.{minor}.{patch}'


def choose(root, commit, tag, yes):
    """The tag to push: --tag or the suggestion, confirmed at the terminal or by --yes."""
    suggested, last = suggest(root)
    tag = tag or suggested
    if yes:
        return tag
    if not sys.stdin.isatty():
        raise ValueError(f'No terminal to confirm the release. To release without asking, run '
                         f'./bilibi release --tag {tag} --yes')
    print(f'The last release on origin is {last}.' if last else 'This is the first release on origin.')
    print(f'Release commit {commit[:7]} as {tag}? Pushing the tag starts the GitHub build. '
          'A version cannot be reused once pushed.')
    while True:
        answer = input(f'Press Enter to release {tag}, type another version such as setup-v1.1.0, or type no to stop: ').strip()
        if answer.lower() in ('', 'y', 'yes'):
            return tag
        if answer.lower() in ('n', 'no'):
            raise ValueError('Release stopped. Nothing was tagged or pushed.')
        try:
            publish.validate_tag(answer)
        except ValueError as error:
            print(error)
            continue
        # Typing the version is the confirmation.
        return answer


def wait_for_build(repository, tag, commit):
    shown = None
    started = time.monotonic()
    print(f'Waiting for GitHub to build {tag}. This usually takes a while; you can stop with Ctrl+C and '
          f'continue later with ./bilibi release --tag {tag}.', flush=True)
    try:
        while True:
            runs = api(f'/repos/{repository}/actions/workflows/release.yml/runs?event=push&branch={tag}&per_page=10')['workflow_runs']
            runs = [run for run in runs if run['head_sha'] == commit]
            if runs:
                build = runs[0]
                if build['html_url'] != shown:
                    shown = build['html_url']
                    print(f'Release setup is building {tag}. You can follow it at {shown}', flush=True)
                if build['status'] == 'completed':
                    if build['conclusion'] == 'success':
                        print('The build passed and GitHub holds a verified draft release.', flush=True)
                        return
                    raise ValueError(
                        f'Release setup did not pass ({build["conclusion"]}): {shown}\n'
                        'Open it to see which step failed. If the fix needs no new commit (for example a missing secret '
                        f'or a GitHub outage), choose Re-run all jobs there, then run ./bilibi release --tag {tag} to continue. '
                        'A fix that changes files needs a new commit and a new version.')
            elif time.monotonic() - started > START_LIMIT_SECONDS:
                raise ValueError(f'GitHub has not started Release setup for {tag}. Check that GitHub Actions is allowed '
                                 f'in this repository, then run ./bilibi release --tag {tag} to continue.')
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        raise ValueError(f'Stopped waiting. The build carries on at GitHub; run ./bilibi release --tag {tag} '
                         'to continue from here.') from None


def finish(repository, tag, root):
    """Download every asset, check it, publish the draft, then say exactly what to upload where."""
    found = publish.find_release(repository, tag)
    folder = root / '.local/releases' / tag
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    publish.gh('release', 'download', tag, '--repo', repository, '--dir', str(folder))
    if found['draft'] and not tag.endswith('-test'):
        expected = publish.release('publish', tag, repository, folder, root)
    else:
        expected, _, _ = publish.verify_download(folder, tag, repository, root)
        publish.verify_assets(found, expected)
        if not found['draft'] and not found.get('immutable'):
            raise ValueError(f'GitHub does not report {tag} as an immutable release. Do not install it; '
                             'enable release immutability in repository Settings and release a new version.')
    target = json.loads((folder / 'recipe.json').read_text())['built_for']['target']
    archive = folder / f'bilibi-{tag}-{target}.tar.gz'
    page = f'https://github.com/{repository}/releases/tag/{tag}'
    print()
    if tag.endswith('-test'):
        print(f'{tag} is a test build, so it stays a draft. Try it on a test server only, then delete the draft and '
              f'its tag: gh-axi release delete {tag} --repo {repository}, git push origin :refs/tags/{tag} and git tag -d {tag}.')
    else:
        print(f'Released {tag}: {page}')
    print(f'''
Install it on each server, Staging first, then Production:
  1. On the server, open Software. Set GitHub repository to {repository} and accept it
     (once per server; a private repository also needs its read token).
  2. Open Software -> Releases and upload this file:
       {archive}
     SHA-256 {expected[archive.name]}, checked against SHA256SUMS and GitHub.
  3. Review the versions it lists, then confirm the install with a fresh 2FA code.
Upload the same file to Staging and Production. The server checks its checksum and
GitHub build provenance again before it installs; Undo on the Software page goes back.''')


def release(tools, root, repository, branch, tag, yes):
    commit = git(root, 'rev-parse', 'HEAD')
    on_origin = remote_tag(root, tag) if tag else None
    if on_origin and on_origin != commit:
        raise ValueError(f'{tag} already exists on origin for commit {on_origin[:7]}. Choose a new release version.')
    github_helper(tools, root)
    if on_origin:
        print(f'{tag} is already on origin for this commit; continuing that release.', flush=True)
        if subprocess.run(['git', 'rev-parse', '--verify', '--quiet', f'refs/tags/{tag}'], cwd=root,
                          stdout=subprocess.DEVNULL).returncode:
            git(root, 'fetch', '--quiet', '--no-tags', 'origin', f'refs/tags/{tag}:refs/tags/{tag}')
    else:
        require_main_check(repository, branch, root)
        tag = choose(root, commit, tag, yes)
        publish.check_new_tag(tag, root)
        publish.start_release(tag, root)
    wait_for_build(repository, tag, commit)
    finish(repository, tag, root)


def repository_name(origin_url, pattern):
    match = pattern.fullmatch(origin_url)
    if not match or match[1].lower() == 'belimbingproject/bilibi-app-maker':
        raise ValueError('origin must be your own application repository on GitHub; see "Start here" in README.md '
                         'to connect it, then rerun ./bilibi release.')
    return match[1]

