#!/usr/bin/env python3
"""Install/check the native recipe, then run its command without a container."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

from arch_prerequisites import prepare as prepare_arch

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(os.environ.get('BILIBI_BUILD_DIR', ROOT / 'build')).resolve()


def read(path):
    with path.open('rb') as stream:
        return tomllib.load(stream)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def expect(name, actual, wanted):
    if actual != wanted:
        raise ValueError(f'{name} is {actual}; this recipe needs {wanted}. Install the pinned version and rerun ./bilibi setup.')


def require_memory():
    available = re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)
    if not available or int(available[1]) < 8 * 1024 * 1024:
        raise ValueError('Build needs at least 8 GiB available memory. Free memory and rerun the command.')


def binary_libraries(binary, baseline):
    """Inspect the distributed ELF itself, never the build host's libc."""
    symbols = output('objdump', '-T', str(binary))
    versions = {tuple(map(int, version.split('.'))) for version in re.findall(r'GLIBC_(\d+(?:\.\d+)+)', symbols)}
    if not versions:
        raise ValueError(f'{binary}: no versioned glibc symbols found')
    required = max(versions)
    if required > tuple(map(int, baseline.split('.'))):
        raise ValueError(f'{binary.name} requires glibc {".".join(map(str, required))}; the recipe baseline is {baseline}')
    return {'glibc_baseline': baseline, 'glibc_required': '.'.join(map(str, required)),
            'dynamic_dependencies': output('objdump', '-p', str(binary))}


def verify_binaries(config):
    target = Path(os.environ.get('CARGO_TARGET_DIR', BUILD / 'target')) / config['target'] / 'release'
    libraries = {name: binary_libraries(target / name, config['glibc'])
                 for name in ('bilibi-web', 'bilibi-installer')}
    evidence = BUILD / 'evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'binary-libraries.json').write_text(json.dumps(libraries, indent=2) + '\n')
    for name, item in libraries.items():
        print(f'{name}: requires glibc {item["glibc_required"]}; baseline {item["glibc_baseline"]}', flush=True)
    return libraries


def prepare(config):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('This build recipe needs Linux x86-64. On Windows, use WSL2.')
    if shutil.which('node'):
        expect('Node', output('node', '--version'), 'v' + config['node'])
    # Reject explicitly installed mismatches before changing packages or tools.
    for command, version in [('zig', config['zig']), ('cargo-zigbuild', config['cargo_zigbuild'])]:
        if shutil.which(command):
            actual = output(command, 'version' if command == 'zig' else '--version').split()[-1]
            expect(command, actual, version)
    prepare_arch(config['postgres'])
    for tool in ('rustup', 'git', 'curl', 'objdump', 'cc', 'psql'):
        if not shutil.which(tool):
            raise ValueError(f'Install {tool}, then rerun ./bilibi setup. See docs/guides/releasing.md.')
    rust = read(ROOT / 'rust-toolchain.toml')['toolchain']['channel']
    if rust not in output('rustup', 'toolchain', 'list').split():
        # Toolchain list usually includes a host suffix.
        if not any(line.split()[0].startswith(rust + '-') for line in output('rustup', 'toolchain', 'list').splitlines()):
            subprocess.run(['rustup', 'toolchain', 'install', rust, '--profile', 'minimal', '--component', 'clippy,rustfmt'], check=True)
    os.environ['RUSTUP_TOOLCHAIN'] = rust
    expect('Rust', output('rustc', '--version').split()[1], rust)
    expect('Cargo', output('cargo', '--version').split()[1], rust)
    subprocess.run(['rustup', 'component', 'add', '--toolchain', rust, 'rustfmt', 'clippy'], check=True)
    subprocess.run(['rustup', 'target', 'add', '--toolchain', rust, config['target']], check=True)
    tools = BUILD / 'native-tools'
    tools.mkdir(parents=True, exist_ok=True)
    node_home = tools / f'node-{config["node"]}'
    # Use the checked archive even when PATH has matching Node: it pins npm too.
    if not (node_home / 'bin/node').exists():
        archive = tools / 'node.tar.xz'
        urllib.request.urlretrieve(f'https://nodejs.org/dist/v{config["node"]}/node-v{config["node"]}-linux-x64.tar.xz', archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != config['node_sha256']:
            raise ValueError('Node download checksum differs from builder.toml')
        node_home.mkdir(exist_ok=True)
        with tarfile.open(archive) as stream:
            # The official archive has one top-level directory.
            stream.extractall(tools, filter='data')
        extracted = tools / f'node-v{config["node"]}-linux-x64'
        node_home.rmdir()
        extracted.rename(node_home)
        archive.unlink()
    os.environ['PATH'] = str(node_home / 'bin') + os.pathsep + os.environ['PATH']
    expect('Node', output('node', '--version'), 'v' + config['node'])
    venv = tools / 'python'
    if not shutil.which('zig') or not shutil.which('cargo-zigbuild'):
        if not (venv / 'bin/python').exists():
            subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        requirements = tools / 'requirements.txt'
        requirements.write_text(f'ziglang=={config["zig"]} --hash=sha256:{config["zig_sha256"]}\n'
                                f'cargo-zigbuild=={config["cargo_zigbuild"]} --hash=sha256:{config["cargo_zigbuild_sha256"]}\n')
        subprocess.run([str(venv / 'bin/python'), '-m', 'pip', '--disable-pip-version-check', '--no-cache-dir', 'install', '--only-binary=:all:', '--require-hashes', '-r', str(requirements)], check=True)
        zig = venv / 'bin/zig'
        if not zig.exists():
            package = subprocess.check_output([str(venv / 'bin/python'), '-c', 'import ziglang; print(ziglang.__path__[0])'], text=True).strip()
            zig.symlink_to(Path(package) / 'zig')
        os.environ['PATH'] = str(venv / 'bin') + os.pathsep + os.environ['PATH']
    expect('Zig', output('zig', 'version'), config['zig'])
    expect('cargo-zigbuild', output('cargo-zigbuild', '--version').split()[-1], config['cargo_zigbuild'])
    os.environ['CARGO_ZIGBUILD_ZIG_PATH'] = shutil.which('zig')
    return {'rustc': output('rustc', '--version'), 'node': output('node', '--version'),
            'npm': output('npm', '--version'),
            'zig': output('zig', 'version'), 'cargo_zigbuild': output('cargo-zigbuild', '--version'),
            'host': platform.platform(), 'host_glibc': platform.libc_ver()}


def main():
    config = read(ROOT / 'builder.toml')
    if sys.argv[1:] == ['--verify-binaries']:
        verify_binaries(config)
        return
    os.chdir(ROOT)
    BUILD.mkdir(parents=True, exist_ok=True)
    temporary = BUILD / 'tmp'
    temporary.mkdir(exist_ok=True)
    os.environ['TMPDIR'] = str(temporary)
    versions = prepare(config)
    evidence = BUILD / 'evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'native-builder.json').write_text(json.dumps(versions, indent=2) + '\n')
    packages = (output('pacman', '-Q') if shutil.which('pacman') else
                output('dpkg-query', '-W') if shutil.which('dpkg-query') else 'No supported package database on this host')
    (evidence / 'builder-packages.txt').write_text(packages + '\n')
    if sys.argv[1:] == ['--setup']:
        print('Native build tools match rust-toolchain.toml and builder.toml.')
        return
    require_memory()
    os.environ.update(CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0', CARGO_PROFILE_RELEASE_STRIP='symbols',
                      BILIBI_GLIBC=config['glibc'], BILIBI_BUILD_DIR=str(BUILD),
                      CARGO_ZIGBUILD_CACHE_DIR=str(BUILD / 'zigbuild-cache'),
                      ZIG_GLOBAL_CACHE_DIR=str(BUILD / 'zig-cache'),
                      npm_config_cache=str(BUILD / 'npm-cache'))
    # Do not inherit flags that bypass Zig or select the build machine's CPU.
    os.environ['RUSTFLAGS'] = f'--remap-path-prefix={ROOT}=/bilibi --remap-path-prefix={Path.home() / ".cargo"}=/cargo'
    os.environ.pop('CARGO_ENCODED_RUSTFLAGS', None)
    if len(sys.argv) < 2:
        raise ValueError('Give a recipe command, or --setup to prepare the tools.')
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
