"""Install Arch build dependencies; native_builder owns the exact tool pins."""
import os
import platform
import shutil
import subprocess


def is_arch():
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return False
    return 'arch' in (release.get('ID', '') + ' ' + release.get('ID_LIKE', '')).split()


def prepare(postgres):
    if not is_arch():
        return
    if not shutil.which('pacman'):
        raise ValueError('Arch setup needs pacman. Install it, then rerun ./bilibi setup.')
    packages = ['base-devel', 'binutils', 'ca-certificates', 'curl', 'git',
                'openssl', 'pkgconf', 'python', 'python-pip', 'postgresql']
    if not shutil.which('rustup'):
        packages.append('rustup')
    missing = [name for name in packages if subprocess.run(
        ['pacman', '-Q', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode]
    if shutil.which('postgres'):
        expect_postgres(subprocess.check_output(['postgres', '--version'], text=True).split()[-1], postgres)
    if not missing:
        return
    privileged = []
    if os.geteuid() != 0:
        if not shutil.which('sudo'):
            raise ValueError('Installing Arch prerequisites needs sudo. Install sudo or run setup as root.')
        privileged = ['sudo']
    info = subprocess.check_output(['pacman', '-Si', 'postgresql'], text=True,
                                   env={**os.environ, 'LC_ALL': 'C'})
    candidate = next((line.split(':', 1)[1].strip() for line in info.splitlines()
                      if line.startswith('Version')), '')
    expect_postgres(candidate.split(':')[-1], postgres)
    print('Installing Arch build prerequisites with pacman.', flush=True)
    # Upgrade in the same transaction: Arch does not support partial upgrades.
    # Constrain PostgreSQL too, so a newly published major aborts resolution
    # before pacman changes installed packages.
    targets = [name for name in missing if name != 'postgresql'] + [f'postgresql={candidate}']
    subprocess.run([*privileged, 'pacman', '-Syu', '--needed', '--noconfirm', *targets], check=True)
    expect_postgres(subprocess.check_output(['postgres', '--version'], text=True).split()[-1], postgres)


def expect_postgres(actual, wanted):
    if actual.split('.')[0] != wanted:
        raise ValueError(f'PostgreSQL is {actual or "unavailable"}; this recipe needs {wanted}. '
                         'Use PostgreSQL 18 packages, then rerun ./bilibi setup.')
