#!/usr/bin/env python3
"""Application workspace commands; component repositories own their local work."""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid

import arch_prerequisites
import native_builder
import recipe
import publish

ROOT, PLATFORM, BUILD = recipe.ROOT, recipe.PLATFORM, recipe.BUILD
TOOLS = Path(__file__).resolve().parent.parent
RECORD_FILES = ('setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock')
OFFICIAL = re.compile(r'https://github\.com/BelimbingProject/[A-Za-z0-9][A-Za-z0-9_.-]*(?:\.git)?')


def run(*args, cwd=ROOT, capture=False):
    return recipe.run(*map(str, args), cwd=cwd, capture=capture)


def repositories():
    yield 'platform', PLATFORM
    if PLATFORM.is_dir():
        for layer in ('domains', 'extensions'):
            parent = PLATFORM / 'apps' / layer
            if parent.is_dir():
                for folder in sorted(parent.iterdir()):
                    if folder.is_dir() and folder.name != 'src' and not folder.name.startswith('.'):
                        yield f'apps/{layer}/{folder.name}', folder


def part_name(name):
    return 'Platform' if name == 'platform' else name.rsplit('/', 1)[-1]


def entries(setup):
    return {'platform': setup['platform'], **{
        f'apps/{layer}/{source["id"]}': source
        for layer in ('domains', 'extensions') for source in setup.get(layer, [])}}


def moved_notice():
    _, saved = recipe.recipe()
    saved = dict(saved)
    moved = []
    for name, folder in repositories():
        if folder.exists():
            recipe.checkout(folder)
            head = run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip()
            if name not in saved or head != saved[name]['commit']:
                moved.append(part_name(name))
    if moved:
        verb = 'has' if len(moved) == 1 else 'have'
        print(f'{", ".join(moved)} {verb} new commits since the last build. Run ./bilibi build to choose versions.')


def status():
    _, sources = recipe.recipe()
    saved = dict(sources)
    print(f'Application: {ROOT}')
    print(run('git', 'status', '--short', '--branch', capture=True), end='')
    found = set()
    for name, folder in repositories():
        found.add(name)
        print(f'\n{name}: {folder}')
        if not folder.exists():
            print('Missing; run ./bilibi setup.')
            continue
        recipe.checkout(folder)
        head = run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip()
        print(run('git', 'status', '--short', '--branch', cwd=folder, capture=True), end='')
        print(f'Commit: {head}')
        origin = run('git', 'remote', 'get-url', 'origin', cwd=folder, capture=True).strip()
        entry = saved.get(name)
        if entry is None:
            print('Not in the saved build record. Run ./bilibi build to include it.')
        elif head != entry['commit'] or origin != entry['url']:
            print(f'Saved: {entry["commit"]} from {entry["url"]}. Run ./bilibi build to choose versions.')
        else:
            print('Matches saved source revision (working changes shown above).')
    moved_notice()
    for name in saved.keys() - found:
        print(f'\n{name}: {PLATFORM / name} — missing; run ./bilibi setup.')


def hooks():
    spec = importlib.util.spec_from_file_location('hooks', TOOLS / 'scripts/install-hooks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install(PLATFORM, ['python3', 'scripts/discover-modules.py', '--check'])
    module.install(ROOT, ['python3', 'bilibi', 'check-staged'],
                   replaces=[['python3', 'scripts/workspace.py', 'check-staged']])


def output_paths():
    BUILD.mkdir(parents=True, exist_ok=True)
    target = Path(os.environ.get('CARGO_TARGET_DIR', BUILD / 'target')).resolve()
    if target.is_relative_to(recipe.SOURCES):
        raise ValueError('CARGO_TARGET_DIR must be outside sources/; use build/target for outputs')
    target.mkdir(parents=True, exist_ok=True)
    # A symlink does not match Platform's directory-only /target/ ignore.
    # Keep this machine-local output link ignored without changing component source.
    exclude = Path(run('git', 'rev-parse', '--git-path', 'info/exclude', cwd=PLATFORM, capture=True).strip())
    if not exclude.is_absolute():
        exclude = PLATFORM / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ''
    if '/target' not in existing.splitlines():
        with exclude.open('a') as stream:
            stream.write('\n# Bilibi workspace output link\n/target\n')
    # Older supported Platform launchers address target/debug directly. This is
    # an output link only: no editable source repository is mounted by symlink.
    link = PLATFORM / 'target'
    if link.is_symlink() and link.resolve() == target:
        pass
    elif link.exists() or link.is_symlink():
        raise ValueError(f'{link}: existing output path left unchanged. Move it to {target} deliberately, then remove the old path and rerun ./bilibi setup.')
    else:
        link.symlink_to(target, target_is_directory=True)
    os.environ['CARGO_TARGET_DIR'] = str(target)


def setup():
    for tool in ('git', 'bash'):
        if not shutil.which(tool):
            raise ValueError(f'Install {tool}, then rerun ./bilibi setup.')
    recipe.materialize()
    hooks()
    output_paths()
    if arch_prerequisites.is_arch():
        # Keep the pinned tools in this process too: start inherits this PATH.
        os.environ['PATH'] = str(Path.home() / '.cargo/bin') + os.pathsep + os.environ['PATH']
        native_builder.prepare(native_builder.read(ROOT / 'builder.toml'))
    os.environ['BILIBI_FRONT_DOOR'] = '1'
    run('bash', TOOLS / 'scripts/dev-setup.sh', PLATFORM)
    run('bash', TOOLS / 'scripts/builder.sh', '--setup')
    print('Setup complete. Local sources and runtime data are preserved. Run ./bilibi start.')


def git_step(folder, *args):
    return shlex.join(['git', '-C', str(folder), *args])


def update_clean(folder, generated=None):
    """Only byte-for-byte known generated output may be replaced by update."""
    recipe.checkout(folder)
    changes = run('git', 'status', '--porcelain=v1', '-z', '--untracked-files=all', cwd=folder, capture=True)
    allowed = []
    for entry in changes.split('\0'):
        if not entry:
            continue
        path = entry[3:]
        if (entry[:2] == ' M' and generated and path in generated
                and not (folder / path).is_symlink()
                and (folder / path).is_file()
                and (folder / path).read_bytes() == generated[path]
                and not run('git', 'diff', '--summary', '--', path, cwd=folder, capture=True)):
            allowed.append(path)
            continue
        raise ValueError(f'{folder}: uncommitted work ({path}); inspect with '
                         f'{git_step(folder, "status", "--short")}, then commit or stash it yourself and rerun ./bilibi update')
    return allowed


def generated_output(command="update"):
    if not PLATFORM.exists():
        return {}
    discovery = recipe.discovery_module()
    try:
        result = {str(path.relative_to(PLATFORM)): text.encode()
                  for path, text in discovery.outputs(PLATFORM, discovery.discovered(PLATFORM)).items()}
    except Exception as error:
        raise ValueError(f'{PLATFORM}: cannot verify generated composition ({error}); run '
                         f'{git_step(PLATFORM, "status", "--short")} and python3 '
                         f'{shlex.quote(str(PLATFORM / "scripts/discover-modules.py"))} --check; '
                         f'resolve the reported source mismatch, then rerun ./bilibi {command}') from error
    # Accept the saved application lock or an intact previously restored build record.
    lock = PLATFORM / 'Cargo.lock'
    for saved in (ROOT / 'composition', PLATFORM / '.local/composition'):
        try:
            if (recipe.digest(saved / 'Cargo.lock') == recipe.read(saved / 'manifest.txt')['cargo_lock_sha256']
                    and lock.read_bytes() == (saved / 'Cargo.lock').read_bytes()):
                result['Cargo.lock'] = lock.read_bytes()
        except (OSError, ValueError, KeyError):
            pass
    return result


def origin_serves(url, commit):
    """A commit held only by a tag or pull request ref is published; an empty scratch repository cannot have it locally."""
    with tempfile.TemporaryDirectory(prefix='bilibi-served-') as scratch:
        run('git', 'init', '--quiet', scratch, cwd=ROOT)
        return subprocess.run(['git', 'fetch', '--quiet', '--depth=1', url, commit], cwd=scratch,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def update():
    """Fast-forward the application, then restore its saved source composition."""
    update_clean(ROOT)
    branch = run('git', 'branch', '--show-current', capture=True).strip()
    if not branch:
        raise ValueError(f'{ROOT}: detached HEAD; run {git_step(ROOT, "switch", "<application-branch>")} and rerun ./bilibi update')
    remote = subprocess.run(['git', 'config', '--get', f'branch.{branch}.remote'], cwd=ROOT,
                            text=True, stdout=subprocess.PIPE, check=False).stdout.strip()
    if remote in ('', '.'):
        raise ValueError(f'{ROOT}: no remote upstream; run {git_step(ROOT, "push", "--set-upstream", "origin", branch)} and rerun ./bilibi update')
    try:
        run('git', 'fetch', '--no-tags', remote)
        upstream = run('git', 'rev-parse', '--symbolic-full-name', '@{upstream}', capture=True).strip()
        target = run('git', 'rev-parse', upstream, capture=True).strip()
    except subprocess.CalledProcessError as error:
        raise ValueError(f'{ROOT}: cannot fetch the application upstream; run {git_step(ROOT, "fetch", remote)}, '
                         'check the branch upstream with git branch -vv, then rerun ./bilibi update') from error
    ahead = run('git', 'rev-list', f'{upstream}..HEAD', capture=True).strip()
    if ahead:
        raise ValueError(f'{ROOT}: commits are not in {upstream}; inspect with {git_step(ROOT, "log", "--oneline", f"{upstream}..HEAD")}, '
                         f'then publish with {git_step(ROOT, "push")} (reconcile any divergence yourself) and rerun ./bilibi update')
    # Read the incoming recipe without changing the application checkout first.
    try:
        with tempfile.TemporaryDirectory(prefix='bilibi-update-') as temp:
            candidate = Path(temp)
            (candidate / 'composition').mkdir()
            for name in ('setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock'):
                data = subprocess.check_output(['git', 'show', f'{target}:{name}'], cwd=ROOT)
                (candidate / name).write_bytes(data)
            _, sources = recipe.recipe(candidate)
            recipe.check_record(candidate)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise ValueError(f'{ROOT}/setup.toml and composition/: incoming saved composition is invalid ({error}); '
                         f'inspect with {git_step(ROOT, "show", target + ":setup.toml")}, '
                         'ask the application maintainer to publish a consistent recipe, manifest and lock, then retry') from error
    saved = dict(sources)
    mounted = list(repositories())
    for _, folder in mounted:
        if folder.is_symlink() or folder.resolve() != folder or (folder.exists() and not folder.is_dir()):
            raise ValueError(f'{folder}: conflicting source path; move it aside deliberately and rerun ./bilibi update')
        if folder.exists():
            recipe.checkout(folder)
    for name, folder in mounted:
        if folder.exists():
            if name != 'platform':
                update_clean(folder)
            else:
                # Reject edits to discovery itself before importing it. The
                # generated bytes provisionally allowed here are verified below.
                pending = {path: (folder / path).read_bytes() for path in recipe.GENERATED
                           if (folder / path).is_file() and not (folder / path).is_symlink()}
                update_clean(folder, pending)
    generated = generated_output()
    replacements = []
    for name, folder in mounted:
        if not folder.exists() and not folder.is_symlink():
            continue
        allowed = update_clean(folder, generated if name == 'platform' else None)
        if name not in saved:
            raise ValueError(f'{folder}: not in the incoming setup.toml; move this clone outside sources/ yourself, '
                             'or run ./bilibi build and release the application record before retrying')
        source = saved[name]
        origin = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=folder,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout.strip()
        if origin != source['url']:
            action = 'set-url' if origin else 'add'
            raise ValueError(f'{folder}: origin differs from setup.toml; inspect with {git_step(folder, "remote", "-v")}; '
                             f'if the saved URL is correct, run {git_step(folder, "remote", action, "origin", source["url"])} and retry')
        replacements.append((folder, source, allowed))
    # Fetch only after every existing repository has passed the edit checks.
    for folder, source, _ in replacements:
        try:
            run('git', 'fetch', '--prune', '--tags', 'origin', cwd=folder)
            unpublished = run('git', 'rev-list', 'HEAD', '--not', '--remotes=origin', cwd=folder, capture=True).strip()
            if unpublished and not origin_serves(source['url'], run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip()):
                raise ValueError(f'{folder}: unpushed commits; inspect with {git_step(folder, "log", "--oneline", "HEAD", "--not", "--remotes=origin")}, '
                                 f'then publish with {git_step(folder, "push", "origin", "HEAD:<branch>")} and rerun ./bilibi update')
            run('git', 'fetch', 'origin', source['commit'], cwd=folder)
        except subprocess.CalledProcessError as error:
            raise ValueError(f'{folder}: cannot obtain saved commit {source["commit"]}; run '
                             f'{git_step(folder, "fetch", "origin", source["commit"])}; verify setup.toml with its owner, then retry') from error
    head = run('git', 'rev-parse', 'HEAD', capture=True).strip()
    if head != target:
        run('git', '-c', 'core.hooksPath=/dev/null', 'merge', '--ff-only', '--no-autostash', target)
        # Re-read the application recipe after pulling. Adopted workspaces keep
        # the already selected immutable maker implementation through this handoff.
        os.execv(sys.executable, [sys.executable, str(TOOLS / 'scripts/workspace.py'), 'update'])
    for folder, source, allowed in replacements:
        # These bytes were proven to be generated, and the index was clean.
        for path in allowed:
            (folder / path).write_bytes(subprocess.check_output(['git', 'show', f'HEAD:{path}'], cwd=folder))
        if run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip() != source['commit']:
            run('git', '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', source['commit'], cwd=folder)
    recipe.materialize()
    recipe.restore()
    print('Updated application and restored saved source commits and composition. Run ./bilibi build to choose new versions.')


def start():
    setup()
    status()
    print('\nStarting local sources with the development seed. The next build will ask about new commits.', flush=True)
    # A development run must not compile a stale release record as its identity.
    # Keep the old machine-local record for inspection rather than deleting it.
    record = PLATFORM / '.local/composition'
    if (record / 'manifest.txt').exists():
        backup = PLATFORM / '.local' / ('composition-before-development-' + uuid.uuid4().hex)
        record.rename(backup)
    os.chdir(PLATFORM)
    os.environ.setdefault('BILIBI_APPLICATION_ROOT', str(ROOT))
    os.execvp('bash', ['bash', '-c', 'if [[ -f $HOME/.cargo/env ]]; then source "$HOME/.cargo/env"; fi; exec bash scripts/start-app.sh'])


def add_explicit(component, kind, source, commit):
    """Keep Software's explicit-source contract in step with Platform add."""
    if component in {'base', 'core', 'web', 'src'}:
        raise ValueError('use a component ID from catalogue.json, for example: ./bilibi add finance')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('--commit must be a full 40-character lowercase commit ID')
    if not OFFICIAL.fullmatch(source):
        raise ValueError(f'{component} is not an official BelimbingProject repository; use the manual mounting guide')
    recipe.materialize()
    hooks()
    folder = PLATFORM / 'apps' / kind / component
    if folder.exists() or folder.is_symlink():
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError(f'{folder} is occupied; move it aside deliberately before adding {component}')
        if Path(run('git', 'rev-parse', '--show-toplevel', cwd=folder, capture=True).strip()).resolve() != folder.resolve():
            raise ValueError(f'{folder} is not its own Git checkout; move it aside deliberately')
        actual = run('git', 'remote', 'get-url', 'origin', cwd=folder, capture=True).strip().removesuffix('.git')
        if actual != source.removesuffix('.git'):
            raise ValueError(f'{folder} has a different origin; inspect it with git -C {folder} remote -v '
                             'and reconcile it deliberately; no files or remotes were changed')
        if run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip() != commit:
            raise ValueError(f'{folder} is not at commit {commit}. Commit or stash its work, then run: '
                             f'git -C {folder} fetch origin && git -C {folder} checkout --detach {commit}; '
                             'nothing was changed')
        print(f'Keeping existing checkout, branch and edits: {folder}', flush=True)
    else:
        run('git', 'clone', '--', source.removesuffix('.git') + '.git', folder)
        try:
            run('git', 'checkout', '--detach', commit, cwd=folder)
        except subprocess.CalledProcessError:
            raise ValueError(f'{source} has no commit {commit}; the new clone was kept at {folder}. '
                             f'Check the commit in Software, then remove that clone (rm -rf {folder}) and retry') from None
    descriptor = tomllib.loads((folder / 'bilibi.container.toml').read_text())
    if descriptor.get('id') != component or descriptor.get('layer') != {'domains': 'domain', 'extensions': 'extension'}[kind]:
        raise ValueError(f'{folder}/bilibi.container.toml does not match the requested component; '
                         'checkout retained for inspection')
    print(f'Added {component} at {folder}. Run ./bilibi start. Commit and push component edits before ./bilibi build.')


def add(component, kind=None, source=None, commit=None):
    if not re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*', component):
        if kind is not None:
            raise ValueError('use a component ID from catalogue.json, for example: ./bilibi add finance')
        raise ValueError('Use a component ID such as finance or supply_chain.')
    if kind is not None:
        return add_explicit(component, kind, source, commit)
    # Obtain missing sources and install the guard even before the first start.
    recipe.materialize()
    hooks()
    catalogue = json.loads((PLATFORM / 'catalogue.json').read_text())
    matches = [(layer, facts) for layer in ('domains', 'extensions')
               if (facts := catalogue.get(layer, {}).get(component)) is not None]
    if len(matches) != 1:
        raise ValueError(f'{component}: choose an unambiguous official Domain or Extension from the Software page')
    layer, facts = matches[0]
    url = facts['repository'].removesuffix('.git') + '.git'
    if not re.fullmatch(r'https://github\.com/BelimbingProject/[A-Za-z0-9_.-]+\.git', url):
        raise ValueError(f'{component}: the catalogue entry is not an official BelimbingProject repository')
    folder = PLATFORM / 'apps' / layer / component
    if folder.exists() or folder.is_symlink():
        raise ValueError(f'{folder}: already exists; local work left unchanged. Run ./bilibi status.')
    # Select the remote's default branch; build can later record this commit.
    listing = run('git', 'ls-remote', url, 'HEAD', capture=True).split()
    if not listing:
        raise ValueError(f'{component}: remote has no HEAD')
    recipe.clone({'url': url, 'commit': listing[0]}, folder)
    print(f'Added {component} at {folder}. Run ./bilibi start. Commit and push component edits before ./bilibi build.')


def selected_recipe():
    setup, _ = recipe.recipe()
    selected = {'format': 1, 'targets': setup['targets'], 'domains': [], 'extensions': []}
    pending = []
    mounted = list(repositories())
    missing = set(entries(setup)) - {name for name, _ in mounted}
    if missing:
        raise ValueError(f'{part_name(sorted(missing)[0])} is missing. Run ./bilibi setup.')
    for name, folder in mounted:
        if not folder.exists():
            raise ValueError(f'{part_name(name)} is missing. Run ./bilibi setup.')
        recipe.checkout(folder)
        changes = run('git', 'status', '--porcelain', '--untracked-files=all', cwd=folder, capture=True)
        for line in changes.splitlines():
            if name != 'platform' or line[3:] not in recipe.GENERATED or line[:2] != ' M':
                raise ValueError(f"{part_name(name)} has changes that aren't committed. Commit and push them first. ({folder}: {line})")
            pending.append(line[3:])
        source = {'url': run('git', 'remote', 'get-url', 'origin', cwd=folder, capture=True).strip(),
                  'commit': run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip()}
        if name == 'platform':
            selected['platform'] = source
        else:
            _, layer, identity = name.split('/')
            selected[layer].append({'id': identity, **source})
    if pending:
        generated = generated_output('build')
        if any(path not in generated or (PLATFORM / path).read_bytes() != generated[path]
               or (PLATFORM / path).is_symlink()
               or run('git', 'diff', '--summary', '--', path, cwd=PLATFORM, capture=True) for path in pending):
            raise ValueError("Platform has changes that aren't committed. Commit and push them first.")
    return selected


def serialize(setup):
    # JSON strings are also TOML basic strings for validated source URLs and IDs.
    lines = ['format = 1', 'targets = ' + json.dumps(setup['targets']), '', '[platform]']
    for key, value in setup['platform'].items():
        lines.append(f'{key} = {json.dumps(value)}')
    for layer in ('domains', 'extensions'):
        for source in setup[layer]:
            lines.extend(['', f'[[{layer}]]'])
            lines.extend(f'{key} = {json.dumps(value)}' for key, value in source.items())
    return '\n'.join(lines) + '\n'


def ci_build():
    return any(os.environ.get(key, '').lower() not in ('', '0', 'false')
               for key in ('CI', 'GITHUB_ACTIONS'))


def choose_versions(yes=False):
    before = {name: (ROOT / name).read_bytes() for name in RECORD_FILES}
    saved, _ = recipe.recipe()
    observed = selected_recipe()
    recipe.validate_recipe(observed)
    old, current = entries(saved), entries(observed)
    moved = []
    # Check every part before asking or saving anything.
    for name, source in current.items():
        previous = old.get(name)
        if previous and source['url'] != previous['url']:
            raise ValueError(f'{part_name(name)} has a different origin from the saved build record. '
                             'Restore its origin before building.')
        if previous != source:
            if not origin_serves(source['url'], source['commit']):
                raise ValueError(f'{part_name(name)} has commits that are not pushed or cannot be fetched from origin. '
                                 'Push them first and check your access to origin.')
            moved.append((name, source, previous))
    if moved and not yes and (ci_build() or not sys.stdin.isatty()):
        names = ', '.join(part_name(name) for name, _, _ in moved)
        verb = 'has' if len(moved) == 1 else 'have'
        raise ValueError(f'{names} {verb} new commits since the last build. '
                         'There is no keyboard. Run this command with --yes to build with them.')
    selected = copy.deepcopy(saved)
    for layer in ('domains', 'extensions'):
        selected.setdefault(layer, [])
    accepted = False
    for name, source, previous in moved:
        previous_short = previous['commit'][:7] if previous else 'not included'
        question = (f'{part_name(name)} has new commits since the last build '
                    f'({previous_short} -> {source["commit"][:7]}). Build with them? (y/n) ')
        answer = 'y' if yes else ''
        while answer not in ('y', 'yes', 'n', 'no'):
            try:
                answer = input(question).strip().lower()
            except EOFError:
                raise ValueError('No answer was received. Run this command with --yes to accept new commits.') from None
        if answer in ('y', 'yes'):
            accepted = True
            if name == 'platform':
                selected['platform'] = source
            else:
                layer = name.split('/')[1]
                selected[layer] = [item for item in selected[layer] if item['id'] != source['id']] + [source]
    return selected, observed, before, accepted


def copy_build_files(candidate):
    for name in ('scripts', 'composition'):
        shutil.copytree((TOOLS if name == 'scripts' else ROOT) / name, candidate / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'node_modules'))
    for name in ('setup.toml', 'builder.toml', 'rust-toolchain.toml'):
        shutil.copyfile(ROOT / name, candidate / name)
    if 'BILIBI_MAKER_SELECTION' in os.environ:
        (candidate / 'maker.lock').write_text(os.environ['BILIBI_MAKER_SELECTION'])


def candidate_environment():
    env = dict(os.environ)
    # A candidate must never restore over the developer's local source branches.
    for key in ('BILIBI_SOURCE_DIR', 'BILIBI_BUILD_DIR', 'CARGO_TARGET_DIR', 'BILIBI_TOOL_APPLICATION_ROOT'):
        env.pop(key, None)
    return env


def build(command, yes=False):
    script = 'scripts/check.sh' if command == 'check' else 'scripts/build.sh'
    target = Path(os.environ.get('CARGO_TARGET_DIR', BUILD / 'target')).resolve()
    if target.is_relative_to(recipe.SOURCES):
        raise ValueError('CARGO_TARGET_DIR must be outside sources/; use build/target for outputs')
    selected, observed, before, accepted = choose_versions(yes)
    recipe.check_record()
    BUILD.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=BUILD.name + '-recipe-', dir=BUILD.parent) as temp:
        candidate = Path(temp)
        copy_build_files(candidate)
        env = candidate_environment()
        env['BILIBI_BUILD_DIR'] = str(BUILD)
        env['CARGO_TARGET_DIR'] = str(target)
        if accepted:
            (candidate / 'setup.toml').write_text(serialize(selected))
            subprocess.run(['python3', str(candidate / 'scripts/recipe.py'), 'materialize'], env=env, check=True)
            subprocess.run(['bash', str(candidate / 'scripts/builder.sh'), 'python3', 'scripts/recipe.py', 'record-candidate'], env=env, check=True)
            recipe.check_record(candidate)
        # Reuse downloads and compiler output, but obtain source from the candidate record.
        subprocess.run(['python3', str(candidate / 'scripts/recipe.py'), 'materialize'], env=env, check=True)
        subprocess.run(['python3', str(candidate / 'scripts/recipe.py'), 'verify-remote'], env=env, check=True)
        subprocess.run(['bash', str(candidate / 'scripts/builder.sh'), script], env=env, check=True)
        if accepted:
            save_record(candidate, observed, before)
        lock = candidate / 'maker.lock'
        if lock.exists() and not ((ROOT / 'maker.lock').is_file() and (ROOT / 'maker.lock').read_bytes() == lock.read_bytes()):
            with tempfile.NamedTemporaryFile(prefix='.bilibi-record-', dir=ROOT, delete=False) as stream:
                pending = Path(stream.name)
                stream.write(lock.read_bytes())
            try:
                pending.chmod(0o644)
                pending.replace(ROOT / 'maker.lock')
            finally:
                pending.unlink(missing_ok=True)
        mark_successful_build(candidate)


def release(tag):
    publish.validate_tag(tag)
    # Refuse unrelated staged, unstaged, renamed, deleted and untracked files.
    changed = set(run('git', 'diff', '--name-only', '-z', 'HEAD', capture=True).split('\0'))
    changed.update(run('git', 'diff', '--cached', '--name-only', '-z', capture=True).split('\0'))
    changed.update(run('git', 'ls-files', '--others', '--exclude-standard', '-z', capture=True).split('\0'))
    changed.discard('')
    if changed:
        raise ValueError('Commit the successful build record, review it on the default branch, and push it before releasing. Changed files: '
                         + ', '.join(sorted(changed)) + '.')
    if not successful_build_matches():
        raise ValueError('Run ./bilibi build first.')
    recipe.check_record()
    publish.check_new_tag(tag, ROOT)
    publish.start_release(tag, ROOT)


def record_fingerprint(root):
    digest = hashlib.sha256()
    for name in RECORD_FILES + (('maker.lock',) if (root / 'maker.lock').exists() else ()):
        digest.update(name.encode() + b'\0' + (root / name).read_bytes() + b'\0')
    return digest.hexdigest()


def successful_build_matches():
    try:
        saved = (ROOT / '.local/successful-build').read_text().strip()
    except OSError:
        return False
    return saved == record_fingerprint(ROOT)


def mark_successful_build(candidate):
    local = ROOT / '.local'
    marker = local / 'successful-build'
    with tempfile.NamedTemporaryFile(prefix='.bilibi-successful-build-', dir=local, mode='w', delete=False) as stream:
        pending = Path(stream.name)
        stream.write(record_fingerprint(candidate) + '\n')
    try:
        pending.replace(marker)
    finally:
        pending.unlink(missing_ok=True)


def save_record(candidate, observed, before):
    recipe.recipe(candidate)
    recipe.check_record(candidate)
    if selected_recipe() != observed:
        raise ValueError('Sources changed while building. The previous build record was kept. Run ./bilibi build again.')
    # Concurrent hand edits must never be overwritten by the prepared candidate.
    if any((ROOT / name).read_bytes() != data for name, data in before.items()):
        raise ValueError('The build record changed while building. Your changes were kept. Run ./bilibi build again.')
    try:
        for name in before:
            replacement = ROOT / name
            with tempfile.NamedTemporaryFile(prefix='.bilibi-record-', dir=replacement.parent, delete=False) as stream:
                pending = Path(stream.name)
                stream.write((candidate / name).read_bytes())
            try:
                pending.chmod(replacement.stat().st_mode & 0o777)
                pending.replace(replacement)
            finally:
                pending.unlink(missing_ok=True)
    except BaseException:
        for name, data in before.items():
            (ROOT / name).write_bytes(data)
        raise
    print('Saved the build versions. Run ./bilibi release --tag setup-vX.Y.Z after checking them.')


def check_staged():
    with tempfile.TemporaryDirectory(prefix='bilibi-index-') as temp:
        index = Path(temp)
        (index / 'composition').mkdir()
        for name in ('setup.toml', 'composition/manifest.txt', 'composition/Cargo.lock'):
            (index / name).write_text(run('git', 'show', ':' + name, capture=True))
        recipe.check_record(index)


def main():
    parser = argparse.ArgumentParser(prog='./bilibi', description=__doc__)
    parser.add_argument('command', nargs='?', choices=('help', 'start', 'update', 'add', 'status', 'setup', 'check', 'build', 'release', 'check-staged'),
                        metavar='{help,start,update,add,status,setup,check,build,release}')
    parser.add_argument('component', nargs='?')
    parser.add_argument('--kind', choices=('domains', 'extensions'), help='add: component mount kind; give with --source and --commit')
    parser.add_argument('--source', help='add: official BelimbingProject repository URL from Software')
    parser.add_argument('--commit', help='add: exact full lowercase commit ID from Software')
    parser.add_argument('--yes', action='store_true', help='build/check: use all new pushed commits without asking')
    parser.add_argument('--tag', help='release: a new version, such as setup-v1.0.0')
    args = parser.parse_args()
    if args.yes and args.command not in ('build', 'check'):
        parser.error('--yes is only for ./bilibi build and ./bilibi check')
    if (args.command == 'release') != (args.tag is not None):
        parser.error('Use ./bilibi release --tag setup-vX.Y.Z; --tag is only for release')
    supplied = (args.kind is not None, args.source is not None, args.commit is not None)
    if any(supplied) and args.command != 'add':
        parser.error('--kind, --source and --commit are only for ./bilibi add')
    if any(supplied) and not all(supplied):
        raise ValueError('--kind, --source and --commit go together; give all three or none')
    if (args.command == 'add') != (args.component is not None):
        parser.error('Use ./bilibi add <component> [--kind domains|extensions --source <url> --commit <sha>]; other commands take no arguments')
    if args.command in (None, 'help'):
        print('This is a Bilibi application: your own product, built on Bilibi.\n'
              'To change Bilibi itself, work in https://github.com/BelimbingProject/bilibi instead.\n\n'
              'Usage: ./bilibi <command>\n\n'
              'Run your application on this computer\n'
              '  start         Install anything missing and run the application. The first\n'
              '                start opens it in your browser.\n'
              '  add <name>    Add a Domain or Extension, such as: ./bilibi add finance\n'
              '                Or paste the add command that the Software page shows.\n'
              '                Then run ./bilibi start again to use it.\n'
              '  status        List the Platform, Domains and Extensions, with their branches,\n'
              '                your uncommitted changes, and any difference from the saved versions.\n\n'
              'Work with your team\n'
              '  update        Update shared maker tooling first (after one-time adoption),\n'
              '                then get your team\'s changes and saved source versions.\n'
              '                No push or pull request; local edits stop the app update.\n'
              '\n'
              'Before a release\n'
              '  check         Choose versions, then build and test against an empty test\n'
              '                database.\n'
              '  build         Ask about new commits, save your choices, and build.\n'
              '                For scripts, use build --yes or check --yes to accept new commits.\n'
              '  release --tag setup-vX.Y.Z\n'
              '                Commit the build record and start the release on GitHub.\n'
              '                docs/guides/releasing.md explains the final publish step.\n\n'
              'Less often\n'
              '  setup         Install the tools and fetch the sources, without starting.\n'
              '  adopt         One-time shared-tooling adoption; see docs/guides/maker-updates.md.\n'
              '  help          Show this list.')
        return
    if args.command == 'status':
        status()
        return
    if args.command == 'check-staged':
        check_staged()
        return
    local = ROOT / '.local'
    local.mkdir(exist_ok=True)
    with (local / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == 'add':
            add(args.component, args.kind, args.source, args.commit)
        elif args.command in ('check', 'build'):
            build(args.command, args.yes)
        elif args.command == 'release':
            release(args.tag)
        else:
            globals()[args.command]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
