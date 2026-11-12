#!/usr/bin/env python3
"""Package checked bytes in the existing Bilibi upload format."""
import argparse
import gzip
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

import recipe
import native_builder


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def archive(path, files, epoch):
    with path.open('wb') as sink, gzip.GzipFile(fileobj=sink, mode='wb', filename='', mtime=0, compresslevel=9) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            for name, (contents, mode) in sorted(files.items()):
                header = tarfile.TarInfo(name)
                header.size = len(contents)
                header.mode = mode
                header.mtime = epoch
                header.uid = header.gid = 0
                header.uname = header.gname = ''
                tar.addfile(header, io.BytesIO(contents))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repository', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'setup-v\d+\.\d+\.\d+(?:-test)?', args.tag):
        parser.error('Tag must be setup-vX.Y.Z or setup-vX.Y.Z-test')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        parser.error('Repository must be owner/name')
    if recipe.run('git', 'status', '--porcelain', cwd=recipe.ROOT, capture=True).strip():
        raise ValueError('Commit the recipe and scripts before packaging')
    recipe.check_sources()
    record = recipe.check_record()
    recipe.run('python3', 'scripts/discover-modules.py', '--check')
    if recipe.digest(recipe.PLATFORM / 'Cargo.lock') != record['cargo_lock_sha256']:
        raise ValueError('The build lock no longer matches the saved build record')
    builder = recipe.read(recipe.ROOT / 'builder.toml')
    toolchain = recipe.read(recipe.ROOT / 'rust-toolchain.toml')
    rust = recipe.run('rustc', '--version', capture=True).strip()
    node = recipe.run('node', '--version', capture=True).strip()
    if rust.split()[1] != toolchain['toolchain']['channel'] or node != 'v' + builder['node']:
        raise ValueError('Compiler or Node differs from rust-toolchain.toml or builder.toml')
    binary_dir = Path(os.environ.get('CARGO_TARGET_DIR', recipe.BUILD / 'target')) / recipe.TARGET / 'release'
    built_toolchain = native_builder.qualified_toolchain(builder, binary_dir)
    binary = binary_dir / 'bilibi-web'
    libraries = native_builder.verify_binaries(builder)
    status = recipe.checked_status(binary)
    commit = recipe.run('git', 'rev-parse', 'HEAD', cwd=recipe.ROOT, capture=True).strip()
    if recipe.run('git', 'rev-list', '-1', args.tag, cwd=recipe.ROOT, capture=True).strip() != commit:
        raise ValueError('The setup tag must name the checked-out recipe commit')
    epoch = int(recipe.run('git', 'show', '-s', '--format=%ct', 'HEAD', cwd=recipe.ROOT, capture=True))
    sources, _ = recipe.recipe()
    setup = {'repository': args.repository, 'commit': commit, 'tag': args.tag, 'sources': sources}
    metadata = {
        'format': 1, 'setup': setup, 'toolchain': toolchain, 'builder': builder,
        'platform': {'version': record['platform_version'], 'commit': record['platform'], 'release': record.get('platform_tag')},
        'domains': [], 'extensions': [], 'status': status,
        'built_for': {'target': recipe.TARGET, 'os': 'linux', 'arch': 'x86_64',
                      'system_library': 'glibc ' + builder['glibc'],
                      'glibc_required': libraries['bilibi-web']['glibc_required']},
        'dynamic_dependencies': libraries['bilibi-web']['dynamic_dependencies'],
        'binaries': libraries,
        'cargo_lock_sha256': record['cargo_lock_sha256'],
    }
    for name, item in sorted(record.get('containers', {}).items()):
        layer, container = name.split('/')
        # reference is the legacy upload preview's commit key. Keep commit too,
        # which is the installer result's existing composition vocabulary.
        metadata[layer].append({'id': container, 'version': item.get('version'),
                                'commit': item['head'], 'reference': item['head']})
    contents = {name: ((recipe.ROOT / 'composition' / name).read_bytes(), 0o644)
                for name in ('manifest.txt', 'Cargo.lock')}
    contents['bilibi-web'] = (binary.read_bytes(), 0o755)
    contents['release.json'] = (json_bytes(metadata), 0o644)
    contents['bundle.json'] = (json_bytes({'format': 1, 'from': f'https://github.com/{args.repository}',
                                          'sha256': recipe.digest(binary), 'setup': setup}), 0o644)
    import hashlib
    sums = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, (data, _) in sorted(contents.items()))
    contents['SHA256SUMS'] = (sums.encode(), 0o644)
    if sum(len(data) for data, _ in contents.values()) > 1024 * 1024 * 1024:
        raise ValueError('Payload exceeds the existing Bilibi unpack limit of 1 GiB')
    output = recipe.ROOT / 'dist'
    if output.exists() and any(output.iterdir()):
        raise ValueError('dist must be empty; move the previous release aside first')
    output.mkdir(exist_ok=True)
    app_asset = output / f'bilibi-{args.tag}-{recipe.TARGET}.tar.gz'
    archive(app_asset, contents, epoch)
    if app_asset.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Archive exceeds the existing Bilibi upload limit of 64 MiB')
    installer = output / f'bilibi-installer-{recipe.TARGET}'
    installer.write_bytes((binary_dir / 'bilibi-installer').read_bytes())
    installer.chmod(0o755)
    # Include the scripts install-server.sh sources, and its referenced service,
    # sudoers, Caddy and waiting-page assets, from the exact Platform revision.
    tool_files = {}
    for path in sorted((recipe.PLATFORM / 'scripts').rglob('*')):
        if path.is_file() and not path.is_symlink():
            relative = str(path.relative_to(recipe.PLATFORM))
            # Only tracked server tooling; never local credentials or caches.
            tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', relative], cwd=recipe.PLATFORM,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if tracked.returncode == 0 and (relative == 'scripts/install-server.sh' or
                                           relative == 'scripts/waiting-page.html' or
                                           relative.startswith(('scripts/lib/', 'scripts/server/'))):
                tool_files[relative] = (path.read_bytes(), 0o755 if path.suffix == '.sh' else 0o644)
    archive(output / 'bilibi-server-tools.tar.gz', tool_files, epoch)
    (output / 'recipe.json').write_bytes(json_bytes(metadata))
    (output / 'builder-packages.txt').write_bytes((recipe.BUILD / 'evidence/builder-packages.txt').read_bytes())
    (output / 'publication.json').write_bytes(json_bytes({
        'format': 1, 'setup': setup,
        'run': f'{os.environ.get("GITHUB_SERVER_URL", "https://github.com")}/{args.repository}/actions/runs/{os.environ.get("GITHUB_RUN_ID", "local")}',
        'attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
        'native_builder': built_toolchain,
    }))
    (output / 'SHA256SUMS').write_text(''.join(f'{recipe.digest(path)}  {path.name}\n' for path in sorted(output.iterdir())))
    print(app_asset)


if __name__ == '__main__':
    main()
