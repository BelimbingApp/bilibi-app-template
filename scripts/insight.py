"""What this copy's run is using right now: processor, memory, disk and database.

Every figure is measured when asked and carries the time it was taken. A figure
that cannot be measured says so, with the reason, instead of showing a guess or
a zero. Nothing is stored and nothing is sent anywhere: this is a look at one
computer, not a monitoring system.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import app_state

ERROR = re.compile(r'\b(error|panicked|failed|refused|fatal|not running)\b', re.I)


def unavailable(reason):
    return {'unavailable': reason}


def process(platform):
    """Processor and memory of this copy's app process, from /proc."""
    try:
        pid = int((Path(platform) / '.local/dev/app.pid').read_text().strip())
        first = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        started = time.monotonic()
        time.sleep(0.25)
        second = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        elapsed = time.monotonic() - started
        ticks = os.sysconf('SC_CLK_TCK')
        used = (int(second[11]) + int(second[12]) - int(first[11]) - int(first[12])) / ticks
        resident = int(Path(f'/proc/{pid}/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        reason = unavailable('The application is not running in this copy, or this system has no /proc.')
        return {'cpu_percent': reason, 'memory_bytes': reason}
    return {'pid': pid, 'cpu_percent': {'value': round(100 * used / elapsed, 1), 'note': 'of one processor, over a quarter second'},
            'memory_bytes': {'value': resident, 'note': 'resident memory of the app process'}}


def disk(folder):
    folder = Path(folder)
    if not folder.exists():
        return unavailable(f'{folder} does not exist yet.')
    if not shutil.which('du'):
        return unavailable('The du command is not installed.')
    try:
        result = subprocess.run(['du', '-sk', '--', str(folder)], capture_output=True, text=True, timeout=30, check=False)
        return {'value': int(result.stdout.split()[0]) * 1024, 'path': str(folder)}
    except subprocess.TimeoutExpired:
        return unavailable(f'Measuring {folder} took longer than 30 seconds.')
    except (OSError, ValueError, IndexError):
        return unavailable(f'{folder} could not be measured.')


def database(platform):
    """The size of this copy's development database. The connection is never returned."""
    if not shutil.which('psql'):
        return unavailable('psql is not installed.')
    env = {**os.environ, 'PGCONNECT_TIMEOUT': '3'}
    target = os.environ.get('DATABASE_URL')
    if not target:
        try:
            port = int((Path(platform) / '.local/postgres18/port').read_text().strip())
        except (OSError, ValueError):
            return unavailable('This copy has no private database yet; it is created by the first start.')
        # The launcher's own fixed local account for the private database.
        target = f'postgres://bilibi@127.0.0.1:{port}/bilibi'
        env['PGPASSWORD'] = 'bilibi'
    try:
        result = subprocess.run(['psql', target, '-X', '-Atqc', 'SELECT pg_database_size(current_database())'],
                                capture_output=True, text=True, timeout=10, env=env, check=False)
        return {'value': int(result.stdout.strip())}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return unavailable('The database did not answer; it runs while the application is started.')


def recent_errors(root, platform, limit=20):
    """Lines from the run's own output that look like errors. They are untrusted text."""
    run = app_state.run_state(root, platform)
    text = run.get('output') or ''
    if run.get('job'):
        text += '\n' + app_state.tail_file(Path(root) / '.local/jobs' / f'{run["job"]}.log', lines=400)
    lines = [line.strip()[:400] for line in text.splitlines() if ERROR.search(line)]
    return list(dict.fromkeys(lines))[-limit:]


def measure(root, platform, build):
    platform = Path(platform)
    run = app_state.run_state(root, platform)
    return {'measured_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'application': str(root),
            'run': {'state': run['state'], 'phase': run.get('phase')},
            **process(platform),
            'disk_bytes': {'sources': disk(Path(root) / 'sources'), 'build': disk(build),
                           'data': disk(platform / '.local'),
                           'note': 'data (database, keys, uploaded files) is inside sources and counted there too'},
            'database_bytes': database(platform),
            'recent_errors': recent_errors(root, platform),
            'recent_errors_note': 'Untrusted text from the run log, credentials removed; newest last.'}


def size(figure):
    if 'value' not in figure:
        return 'unavailable: ' + figure['unavailable']
    value = float(figure['value'])
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def render(measured):
    cpu, memory = measured['cpu_percent'], measured['memory_bytes']
    lines = [f'Measured {measured["measured_at"]} for {measured["application"]}',
             f'Run: {measured["run"]["state"]}',
             'Processor: ' + (f'{cpu["value"]}% {cpu["note"]}' if 'value' in cpu else 'unavailable: ' + cpu['unavailable']),
             'Memory: ' + size(memory)]
    lines += [f'Disk, {name}: {size(measured["disk_bytes"][name])}' for name in ('sources', 'build', 'data')]
    lines.append('Database: ' + size(measured['database_bytes']))
    lines.append('Recent errors: ' + ('none found' if not measured['recent_errors'] else ''))
    lines += ['  ' + line for line in measured['recent_errors']]
    return '\n'.join(lines)
