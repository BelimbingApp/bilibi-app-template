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
import tempfile
import tomllib
import urllib.request

from arch_prerequisites import prepare as prepare_arch

ROOT = Path(os.environ.get('BILIBI_TOOL_APPLICATION_ROOT', Path(__file__).resolve().parent.parent)).resolve()
BUILD = Path(os.environ.get('BILIBI_BUILD_DIR', ROOT / 'build')).resolve()


def read(path):
    with path.open('rb') as stream:
        return tomllib.load(stream)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def expect(name, actual, wanted):
    if actual != wanted:
        raise ValueError(f'{name} is {actual}; this recipe needs {wanted}. Install the pinned version and rerun ./bilibi setup.')


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


def installed_node():
    # Platform assets/package.json and dev-setup.sh require Node >=22. All
    # engines in the recorded asset lock accept that range; prereleases do not
    # satisfy it. npm must also run under the selected Node.
    try:
        version = output('node', '--version')
        match = re.fullmatch(r'v(\d+)\.\d+\.\d+', version)
        return bool(match and int(match[1]) >= 22
                    and re.fullmatch(r'\d+\.\d+\.\d+', output('npm', '--version')))
    except (OSError, subprocess.CalledProcessError):
        return False


def ci():
    return any(os.environ.get(key, '').lower() not in ('', '0', 'false')
               for key in ('CI', 'GITHUB_ACTIONS'))


def node_digest(home):
    digest = hashlib.sha256()
    for path in sorted(home.rglob('*')):
        if path.name == '.bilibi-verified':
            continue
        name = str(path.relative_to(home))
        digest.update(name.encode() + b'\0')
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(str(path.stat().st_mode & 0o777).encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def select_node(config, tools, development=False):
    node_home = tools / f'node-{config["node"]}'
    if (development and not ci() and installed_node()
            and Path(shutil.which('node')).resolve() != (node_home / 'bin/node').resolve()):
        return 'installed'
    tools.mkdir(parents=True, exist_ok=True)
    verification = node_home / '.bilibi-verified'
    if verification.is_file():
        expected = config['node_sha256'] + ':' + node_digest(node_home)
        if verification.read_text().strip() != expected:
            raise ValueError(f'{node_home}: private Node changed; move this cache aside and retry')
    else:
        archive = tools / 'node.tar.xz'
        urllib.request.urlretrieve(f'https://nodejs.org/dist/v{config["node"]}/node-v{config["node"]}-linux-x64.tar.xz', archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != config['node_sha256']:
            raise ValueError('Node download checksum differs from builder.toml')
        with tempfile.TemporaryDirectory(prefix='node-install-', dir=tools) as temp:
            with tarfile.open(archive) as stream:
                stream.extractall(temp, filter='data')
            extracted = Path(temp) / f'node-v{config["node"]}-linux-x64'
            (extracted / '.bilibi-verified').write_text(config['node_sha256'] + ':' + node_digest(extracted) + '\n')
            if node_home.exists():
                shutil.rmtree(node_home)
            extracted.rename(node_home)
        archive.unlink()
    os.environ['PATH'] = str(node_home / 'bin') + os.pathsep + os.environ['PATH']
    expect('Node', output('node', '--version'), 'v' + config['node'])
    return 'pinned'


def build_toolchain(config):
    """Tie tool versions to these compiled bytes, independently of later setup."""
    target = Path(os.environ.get('CARGO_TARGET_DIR', BUILD / 'target')) / config['target'] / 'release'
    evidence = BUILD / 'evidence'
    versions = json.loads((evidence / 'native-builder.json').read_text())
    versions['binaries'] = {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                            for name in ('bilibi-web', 'bilibi-installer')}
    (evidence / 'build-toolchain.json').write_text(json.dumps(versions, indent=2) + '\n')


def qualified_toolchain(config, target):
    versions = json.loads((BUILD / 'evidence/build-toolchain.json').read_text())
    if (versions.get('node_selection') != 'pinned' or versions.get('node') != 'v' + config['node']
            or versions.get('node_archive_sha256') != config['node_sha256']):
        raise ValueError('Packaging requires a build with the pinned Node archive; CI rebuilds the checked record.')
    for name in ('bilibi-web', 'bilibi-installer'):
        if versions['binaries'][name] != hashlib.sha256((target / name).read_bytes()).hexdigest():
            raise ValueError(f'{name}: bytes differ from the recorded build toolchain; rebuild before packaging')
    return versions


def prepare(config, development=False):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('This build recipe needs Linux x86-64. On Windows, use WSL2.')
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
    node_selection = select_node(config, tools, development)
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
            'node_selection': node_selection,
            'node_archive_sha256': config['node_sha256'] if node_selection == 'pinned' else None,
            'npm': output('npm', '--version'),
            'zig': output('zig', 'version'), 'cargo_zigbuild': output('cargo-zigbuild', '--version'),
            'host': platform.platform(), 'host_glibc': platform.libc_ver()}


def main():
    config = read(ROOT / 'builder.toml')
    if sys.argv[1:] == ['--record-build']:
        build_toolchain(config)
        return
    if sys.argv[1:] == ['--verify-binaries']:
        verify_binaries(config)
        return
    os.chdir(ROOT)
    BUILD.mkdir(parents=True, exist_ok=True)
    temporary = BUILD / 'tmp'
    temporary.mkdir(exist_ok=True)
    os.environ['TMPDIR'] = str(temporary)
    development = sys.argv[1:2] == ['--development']
    if development:
        del sys.argv[1]
    versions = prepare(config, development=development)
    evidence = BUILD / 'evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'native-builder.json').write_text(json.dumps(versions, indent=2) + '\n')
    packages = (output('pacman', '-Q') if shutil.which('pacman') else
                output('dpkg-query', '-W') if shutil.which('dpkg-query') else 'No supported package database on this host')
    (evidence / 'builder-packages.txt').write_text(packages + '\n')
    if sys.argv[1:] == ['--setup']:
        print(f'Native tools ready; Node {versions["node"]} ({versions["node_selection"]}), npm {versions["npm"]}.')
        return
    os.environ.update(CARGO_INCREMENTAL='0', CARGO_PROFILE_RELEASE_STRIP='symbols',
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
