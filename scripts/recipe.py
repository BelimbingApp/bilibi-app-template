#!/usr/bin/env python3
"""Restore and verify the application’s saved build record."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit

ROOT = Path(os.environ.get('BILIBI_TOOL_APPLICATION_ROOT', Path(__file__).resolve().parent.parent)).resolve()
BUILD = Path(os.environ.get('BILIBI_BUILD_DIR', ROOT / 'build')).resolve()
SOURCES = Path(os.environ.get('BILIBI_SOURCE_DIR', ROOT / 'sources')).resolve()
PLATFORM = SOURCES / 'platform'
if BUILD == SOURCES or BUILD.is_relative_to(SOURCES) or SOURCES.is_relative_to(BUILD):
    raise SystemExit('Keep BILIBI_SOURCE_DIR and BILIBI_BUILD_DIR separate; sources must never live under disposable build outputs')
TARGET = 'x86_64-unknown-linux-gnu'
GENERATED = {'Cargo.lock'} | {
    f'apps/{layer}/{name}' for layer in ('base', 'core', 'domains', 'extensions')
    for name in ('Cargo.toml', 'src/lib.rs')
}


def run(*args, cwd=PLATFORM, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def read(path):
    with path.open('rb') as stream:
        return tomllib.load(stream)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def recipe(root=ROOT):
    return validate_recipe(read(root / 'setup.toml'))


def validate_recipe(setup):
    if set(setup) - {'format', 'targets', 'platform', 'domains', 'extensions'}:
        raise ValueError('Unknown setup.toml keys')
    if setup.get('format') != 1 or setup.get('targets') != [TARGET]:
        raise ValueError(f'Use format = 1 and targets = ["{TARGET}"]')
    sources = [('platform', setup['platform'])]
    for layer in ('domains', 'extensions'):
        seen = set()
        for source in setup.get(layer, []):
            name = source['id']
            if not re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*', name) or name in seen:
                raise ValueError(f'Invalid or duplicate {layer} id: {name}')
            seen.add(name)
            sources.append((f'apps/{layer}/{name}', source))
    for path, source in sources:
        expected = {'url', 'commit'} | ({'id'} if path != 'platform' else set())
        if set(source) != expected:
            raise ValueError(f'{path}: expected keys {sorted(expected)}')
        url = urlsplit(source['url'])
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.port or not url.path.endswith('.git')):
            raise ValueError(f'{path}: use an HTTPS Git URL without credentials, query or fragment')
        if not re.fullmatch('[0-9a-f]{40}', source['commit']):
            raise ValueError(f'{path}: commit must be a full lowercase 40-character commit ID')
    return setup, sources


def check_sources():
    _, sources = recipe()
    for path, source in sources:
        folder = PLATFORM if path == 'platform' else PLATFORM / path
        if not folder.exists():
            raise ValueError(f'{folder}: missing; run ./bilibi setup')
        if folder.is_symlink() or Path(run('git', 'rev-parse', '--show-toplevel', cwd=folder, capture=True).strip()).resolve() != folder:
            raise ValueError(f'{path}: expected an independent checkout')
        if run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip() != source['commit']:
            raise ValueError(f'{folder}: HEAD differs from the saved build record; run ./bilibi build to choose versions, or check out {source["commit"]}')
        if run('git', 'remote', 'get-url', 'origin', cwd=folder, capture=True).strip() != source['url']:
            raise ValueError(f'{path}: origin differs from setup.toml')
        # Only the discover-modules outputs may differ in the Platform checkout.
        changes = run('git', 'status', '--porcelain', '--untracked-files=all', cwd=folder, capture=True)
        for line in changes.splitlines():
            if path != 'platform' or line[3:] not in GENERATED or line[:2] not in (' M', 'M '):
                raise ValueError(f'{path}: unexpected working tree change: {line}')
    expected = {path for path, _ in sources if path != 'platform'}
    actual = {str(folder.relative_to(PLATFORM))
              for layer in ('domains', 'extensions')
              for folder in (PLATFORM / 'apps' / layer).iterdir()
              if folder.is_dir() and not folder.name.startswith('.') and folder.name != 'src'}
    if actual != expected:
        raise ValueError(f'Mounted source set differs: expected {expected}, found {actual}')


def checkout(folder):
    if folder.is_symlink() or Path(run('git', 'rev-parse', '--show-toplevel', cwd=folder, capture=True).strip()).resolve() != folder:
        raise ValueError(f'{folder}: expected an independent checkout; move the conflicting path aside yourself')


def clone(source, folder):
    """Only an absent destination is ours to populate; failures leave no partial clone."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.clone-', dir=folder.parent) as temp:
        pending = Path(temp) / 'repository'
        run('git', 'clone', '--no-checkout', '--', source['url'], str(pending), cwd=folder.parent)
        run('git', 'fetch', '--tags', 'origin', source['commit'], cwd=pending)
        run('git', 'checkout', '--detach', source['commit'], cwd=pending)
        if folder.exists() or folder.is_symlink():
            raise ValueError(f'{folder}: destination appeared during cloning; left unchanged')
        pending.rename(folder)


def materialize():
    _, sources = recipe()
    for path, source in sources:
        folder = PLATFORM if path == 'platform' else PLATFORM / path
        if folder.resolve() != folder:
            raise ValueError(f'{folder}: source path traverses a symlink; keep ordinary independent clones')
        if not folder.exists() and not folder.is_symlink():
            clone(source, folder)
        else:
            # Setup is deliberately offline for existing repositories. Never fetch,
            # switch, reset, clean or restore a developer's existing working copy.
            checkout(folder)
            head = run('git', 'rev-parse', 'HEAD', cwd=folder, capture=True).strip()
            origin = run('git', 'remote', 'get-url', 'origin', cwd=folder, capture=True).strip()
            if head != source['commit'] or origin != source['url']:
                print(f'{folder}: differs from the saved build record. Run ./bilibi build to choose versions.')


def discovery_module():
    module_spec = importlib.util.spec_from_file_location('discovery', PLATFORM / 'scripts/discover-modules.py')
    discovery = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(discovery)
    return discovery


def verify_remote():
    """Prove remote availability and version labels without local objects or credentials on disk."""
    _, sources = recipe()
    record = check_record()
    BUILD.mkdir(parents=True, exist_ok=True)
    discovery = discovery_module()
    with tempfile.TemporaryDirectory(prefix='source-proof-', dir=BUILD) as temp:
        for index, (path, source) in enumerate(sources):
            folder = Path(temp) / str(index)
            clone(source, folder)
            if path == 'platform':
                expected = (record.get('platform_tag') or '')[1:] or None
            else:
                expected = record['containers'][path.removeprefix('apps/')].get('version')
            if discovery.version_at_head(path, folder) != expected:
                raise ValueError(f'{path}: remote version tags differ from the saved build record')


def check_record(root=ROOT):
    _, sources = recipe(root)
    record = read(root / 'composition/manifest.txt')
    if record['platform'] != sources[0][1]['commit']:
        raise ValueError('The saved build record names another Platform commit; restore the three record files together')
    containers = {path.removeprefix('apps/'): source['commit'] for path, source in sources[1:]}
    if {name: item['head'] for name, item in record.get('containers', {}).items()} != containers:
        raise ValueError('The saved build record names other Domains or Extensions; restore the three record files together')
    if digest(root / 'composition/Cargo.lock') != record['cargo_lock_sha256']:
        raise ValueError('The saved Cargo.lock does not match the saved build record')
    return record


def restore():
    check_sources()
    record = check_record()
    target = PLATFORM / '.local/composition'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('manifest.txt', 'Cargo.lock'):
        source = ROOT / 'composition' / name
        if not (target / name).exists() or source.read_bytes() != (target / name).read_bytes():
            shutil.copyfile(source, target / name)
    run('python3', 'scripts/discover-modules.py', '--restore')
    run('python3', 'scripts/discover-modules.py', '--check')
    # Compare all saved version tags with the tags actually fetched.
    discovery = discovery_module()
    tag = record.get('platform_tag')
    if discovery.version_at_head('Platform', PLATFORM) != (tag[1:] if tag else None):
        raise ValueError('Platform version tags changed since the build record was saved')
    for name, item in record.get('containers', {}).items():
        if discovery.version_at_head(name, PLATFORM / 'apps' / name) != item.get('version'):
            raise ValueError(f'{name}: version tags changed since the build record was saved')
    check_sources()


def record_candidate():
    check_sources()
    run('python3', 'scripts/discover-modules.py')
    run('bash', 'scripts/build-assets.sh', '--check')
    run('cargo', 'metadata', '--format-version', '1', capture=True)
    run('python3', 'scripts/discover-modules.py', '--pin')
    (ROOT / 'composition').mkdir(exist_ok=True)
    for name in ('manifest.txt', 'Cargo.lock'):
        shutil.copyfile(PLATFORM / '.local/composition' / name, ROOT / 'composition' / name)
    restore()


def checked_status(binary):
    record = check_record()
    status = run(str(binary.resolve()), 'status', capture=True)
    lines = status.splitlines()
    if len(lines) < 2 or lines[1] != 'Pin: .local/composition, compiled in at build time':
        raise ValueError('The built program has no valid build record compiled into it')
    expected = f'Bilibi {record["platform_version"]}, '
    expected += f'release {record["platform_tag"]}' if record.get('platform_tag') else 'unreleased'
    expected += f', commit {record["platform"]}'
    if lines[0] != expected:
        raise ValueError('Program Platform identity differs from the saved recipe')
    expected_lines = []
    for layer, heading in (('domains', 'Domains'), ('extensions', 'Extensions')):
        entries = [(name.split('/')[1], item) for name, item in record.get('containers', {}).items() if name.startswith(layer + '/')]
        expected_lines.append(heading + (':' if entries else ': none mounted'))
        for name, item in sorted(entries):
            expected_lines.append(f'  {name} {item.get("version", "untagged")}, commit {item["head"]}, modules:')
    actual = lines[2:]
    if len(expected_lines) != len(actual) or any(
            not (got.startswith(want + ' ') if want.endswith('modules:') else got == want)
            for want, got in zip(expected_lines, actual)):
        raise ValueError('Program Domains or Extensions differ from the saved recipe')
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('materialize', 'record-candidate', 'restore', 'verify', 'verify-remote', 'check-record', 'status'))
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    if args.command == 'status':
        print(checked_status(args.binary or Path(os.environ.get('CARGO_TARGET_DIR', BUILD / 'target')) / TARGET / 'release/bilibi-web'), end='')
    elif args.command == 'verify':
        check_sources()
        check_record()
        run('python3', 'scripts/discover-modules.py', '--check')
        if digest(PLATFORM / 'Cargo.lock') != digest(ROOT / 'composition/Cargo.lock'):
            raise ValueError('The restored lock changed during validation')
    else:
        globals()[args.command.replace('-', '_')]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
