#!/usr/bin/env python3
"""Run one ./bilibi command in the background and keep its outcome inspectable.

A job is the plain command, `./bilibi <arguments>`, run by a small detached
runner that writes the output to .local/jobs/<id>.log and the exit code to
.local/jobs/<id>.json. Nothing else knows how to add, pin, check, build or
start: use launch() here instead of writing a second way to run a command.

The runner outlives whoever launched it, so a closed terminal or a
disconnected MCP client loses nothing: ./bilibi jobs and ./bilibi logs read the
same files. A job whose runner disappeared without recording an exit code is
reported as interrupted, never as succeeded or failed.
"""
import fcntl
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

import app_state

KEEP = 30


def folder(root):
    return Path(root) / '.local/jobs'


def describe(job, root):
    """A job record with its state worked out: running, succeeded, failed or interrupted."""
    job = dict(job)
    if job.get('exit_code') is not None:
        job['state'] = 'succeeded' if job['exit_code'] == 0 else 'failed'
        if job.get('kind') == 'run' and job['exit_code'] in (130, 143):
            job['state'] = 'stopped'  # A run ends by being stopped; that is not a failure.
    elif job.get('refused'):
        job['state'] = 'failed'
    elif app_state.alive(job.get('runner')) or time.time() - job.get('started_at', 0) < 5 and not job.get('runner'):
        job['state'] = 'running'
    else:
        job['state'] = 'interrupted'
        job['detail'] = ('The job stopped without recording a result, for example because the computer restarted. '
                         'Its outcome is uncertain: read its log and the status before running it again.')
    job['log'] = str(folder(root) / f'{job["id"]}.log')
    job.pop('runner', None)
    return job


def read(root, identity):
    if not isinstance(identity, str) or not identity.replace('-', '').isalnum():
        return None
    job = app_state.read_json(folder(root) / f'{identity}.json')
    return describe(job, root) if job and job.get('id') == identity else None


def listing(root, limit=10):
    """The newest jobs first."""
    found = sorted(folder(root).glob('*.json'), reverse=True) if folder(root).is_dir() else []
    jobs = [job for path in found[:limit] if (job := read(root, path.stem))]
    return jobs


def running(root):
    """The changing job in progress, or None. Background runs of the application do not count."""
    for job in listing(root, KEEP):
        if job['state'] == 'running' and job.get('kind') != 'run':
            return job
    return None


def log(root, identity, lines=200):
    return app_state.tail_file(folder(root) / f'{identity}.log', lines)


def prune(root):
    for path in sorted(folder(root).glob('*.json'), reverse=True)[KEEP:]:
        job = read(root, path.stem)
        if job and job['state'] == 'running':
            continue
        path.unlink(missing_ok=True)
        path.with_suffix('.log').unlink(missing_ok=True)


def launch(root, arguments, kind='change'):
    """Start `./bilibi <arguments>` detached and return its job record at once.

    Only one changing job runs in a checkout at a time; a second one is refused
    with the first one's ID rather than queued behind it. kind='run' is the
    long-lived application run, which other jobs may work beside."""
    root = Path(root)
    jobs = folder(root)
    jobs.mkdir(parents=True, exist_ok=True)
    lock = None
    if kind != 'run':
        lock = os.open(jobs / 'changing.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock)
            busy = running(root)
            raise ValueError(f'Job {busy["id"]} (./bilibi {" ".join(busy["command"])}) is still running in this copy; '
                             'wait for it, then try again.' if busy else
                             'Another change is still running in this copy; wait for it, then try again.') from None
    try:
        identity = time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)
        job = {'id': identity, 'kind': kind, 'command': list(arguments), 'started_at': time.time(),
               'finished_at': None, 'exit_code': None}
        app_state.write_json(jobs / f'{identity}.json', job)
        (jobs / f'{identity}.log').touch()
        detached = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root), identity],
                                    cwd=root, start_new_session=True, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    pass_fds=() if lock is None else (lock,), close_fds=True)
        # Collect the runner when it ends, so a long-lived launcher keeps no dead children.
        threading.Thread(target=detached.wait, daemon=True).start()
    finally:
        if lock is not None:
            os.close(lock)  # The runner inherited the lock and holds it until the command ends.
    prune(root)
    return describe(job, root)


def wait(root, identity, seconds):
    """Give a short job the chance to finish, so one call can report its result."""
    deadline = time.monotonic() + seconds
    while True:
        job = read(root, identity)
        if job is None or job['state'] != 'running' or time.monotonic() >= deadline:
            return job
        time.sleep(0.2)


def runner(root, identity):
    """The detached half of launch(): run the command, then record how it ended."""
    root = Path(root)
    record = folder(root) / f'{identity}.json'
    job = app_state.read_json(record)
    if not job:
        return 1
    job['runner'] = {'pid': os.getpid(), 'start': app_state.process_start(os.getpid())}
    app_state.write_json(record, job)
    # No terminal: Git must fail on a refused credential instead of waiting for
    # one. The person's own credential helper still answers.
    env = {**os.environ, 'BILIBI_JOB_ID': identity, 'GIT_TERMINAL_PROMPT': '0'}
    with open(folder(root) / f'{identity}.log', 'ab') as output:
        try:
            code = subprocess.run([str(root / 'bilibi'), *job['command']], cwd=root, env=env,
                                  stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT).returncode
        except OSError as error:
            output.write(f'Could not run ./bilibi: {error}\n'.encode())
            code = 127
    job.update(exit_code=code if code >= 0 else 128 - code, finished_at=time.time())
    app_state.write_json(record, job)
    return 0


if __name__ == '__main__':
    sys.exit(runner(sys.argv[1], sys.argv[2]))
