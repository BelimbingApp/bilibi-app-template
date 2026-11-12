"""State, sanitizing and background jobs, without a network, a compiler or a real application."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import app_state
import insight
import jobs

PRIVATE_KEY = ('-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAsecretsecretsecret\nmorekeymaterial\n'
               '-----END RSA PRIVATE KEY-----')
SECRET_LOG = f'''Cloning into 'pricing'...
remote: https://x-access-token:ghs_abcdefghijklmnopqrstuvwxyz0123@github.com/acme/pricing.git
fatal: Authentication failed for 'https://oauth2:glpat-12345678901234567890@gitlab.example/acme/pricing.git/'
DATABASE_URL=postgres://bilibi:hunter2@127.0.0.1:5438/bilibi
connecting with postgres://bilibi:hunter2@127.0.0.1:5438/bilibi
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature
password = "correct horse"
SOURCE_READ_TOKEN: github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz
token ghp_0123456789abcdefghijklmnopqrstuvwxyzAB leaked in a sentence
{PRIVATE_KEY}
\x1b[31merror\x1b[0m: could not compile `bilibi-web`
'''
SECRETS = ('ghs_abcdefghijklmnopqrstuvwxyz0123', 'glpat-12345678901234567890', 'hunter2', 'eyJhbGciOiJIUzI1NiJ9',
           'correct', 'github_pat_11ABCDEFG', 'ghp_0123456789', 'MIIEowIBAAKCAQEA', 'morekeymaterial',
           'env-only-secret-value')


class SanitizeTest(unittest.TestCase):
    def test_secret_bearing_log_keeps_the_error_and_loses_every_secret(self):
        with mock.patch.dict(os.environ, {'SOURCE_APP_PRIVATE_KEY': 'env-only-secret-value',
                                          'BILIBI_CHECK_DATABASE_URL': 'postgresql://checker@db.internal/check'}):
            cleaned = app_state.sanitize(SECRET_LOG + 'key is env-only-secret-value at postgresql://checker@db.internal/check\n')
        for secret in SECRETS + ('db.internal',):
            self.assertNotIn(secret, cleaned)
        self.assertIn('error: could not compile `bilibi-web`', cleaned)
        self.assertIn('github.com/acme/pricing.git', cleaned)
        self.assertNotIn('\x1b', cleaned)

    def test_tail_is_bounded_in_lines_and_bytes(self):
        text = '\n'.join(f'line {number} ' + 'x' * 50 for number in range(5000))
        self.assertEqual(len(app_state.tail(text, lines=20).splitlines()), 20)
        self.assertLessEqual(len(app_state.tail(text, lines=5000, size=1000)), 1000)
        self.assertTrue(app_state.tail(text, lines=3).endswith('line 4999 ' + 'x' * 50))

    def test_unterminated_private_key_is_removed_to_the_end(self):
        self.assertNotIn('MIIE', app_state.sanitize('-----BEGIN PRIVATE KEY-----\nMIIEabc'))

    def test_key_cut_by_the_log_window_is_redacted_in_every_log_reader(self):
        fragment = 'KEYBODY0001' + 'A' * 40 + '\n-----END PRIVATE KEY-----\nerror: after the key\n'
        cleaned = app_state.sanitize(fragment)
        self.assertNotIn('KEYBODY', cleaned)
        self.assertNotIn('END PRIVATE KEY', cleaned)
        self.assertIn('error: after the key', cleaned)
        window = 4 * 16 * 1024
        key = '-----BEGIN PRIVATE KEY-----\n' + fragment
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.local/jobs').mkdir(parents=True)
            raw = (key + 'x\n' * (window // 2)).encode()
            (root / '.local/jobs/job1.log').write_bytes(raw)
            for read in (app_state.tail_file(root / '.local/jobs/job1.log'), jobs.log(root, 'job1')):
                self.assertNotIn('KEYBODY', read)
                self.assertNotIn('END PRIVATE KEY', read)

class AddressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def address(self, shell=None):
        env = {} if shell is None else {'BILIBI_DEV_HOST': shell}
        with mock.patch.dict(os.environ, env, clear=True):
            return app_state.address(self.root, self.root / 'sources/platform')

    def test_default_never_comes_from_the_folder_or_origin(self):
        found = self.address()
        self.assertEqual((found['host'], found['url'], found['set_by']), ('bilibi.localhost', 'https://bilibi.localhost', 'the default'))
        self.assertIn(f'BILIBI_DEV_HOST=<name>.localhost in {self.root}/.env', found['rename'])

    def test_env_file_gives_only_the_host_and_the_shell_wins(self):
        (self.root / '.env').write_text('# local\nSOME_PASSWORD=hunter2\nexport BILIBI_DEV_HOST="my-sbg.localhost"\n')
        found = self.address()
        self.assertEqual(found['url'], 'https://my-sbg.localhost')
        self.assertNotIn('hunter2', json.dumps(found))
        self.assertEqual(self.address('other.localhost')['set_by'], 'the shell')
        self.assertIn('problem', self.address('example.com'))


class RunStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform = self.root / 'sources/platform'
        (self.platform / '.local/dev').mkdir(parents=True)

    def launcher(self, answer):
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        previous = os.getcwd()
        os.chdir(self.platform / '.local/dev')
        try:
            server.bind('control.sock')
        finally:
            os.chdir(previous)
        server.listen()

        def serve():
            try:
                while True:
                    connection, _ = server.accept()
                    connection.recv(32)
                    connection.sendall(answer)
                    connection.close()
            except OSError:
                pass
        threading.Thread(target=serve, daemon=True).start()

    def test_stopped_without_launcher_or_record(self):
        self.assertEqual(app_state.run_state(self.root, self.platform)['state'], 'stopped')

    def test_a_reused_process_id_is_not_this_copys_run(self):
        app_state.write_json(self.root / '.local/run.json', {'pid': os.getpid(), 'start': 1, 'started_at': 0})
        self.assertEqual(app_state.run_state(self.root, self.platform)['state'], 'stopped')

    def test_recorded_run_before_the_launcher_is_starting(self):
        with mock.patch.dict(os.environ, {'BILIBI_JOB_ID': 'job-1'}):
            app_state.record_run(self.root)
        run = app_state.run_state(self.root, self.platform)
        self.assertEqual((run['state'], run['phase'], run['pid'], run['job']), ('starting', 'prepare', os.getpid(), 'job-1'))

    def test_ready_failed_and_foreign_runs(self):
        status = self.platform / '.local/dev-run.status.json'
        self.launcher(b'0')
        status.write_text(json.dumps({'phase': 'serve', 'failed': False, 'output': ''}))
        run = app_state.run_state(self.root, self.platform)
        self.assertEqual((run['state'], run['ready'], run['pid']), ('running', True, None))
        self.assertIn('press Ctrl-C in the terminal', run['detail'])
        status.write_text(json.dumps({'phase': 'build', 'failed': True,
                                      'output': 'error[E0432]: unresolved import\nDATABASE_URL=postgres://bilibi:hunter2@h/db'}))
        app_state.record_run(self.root)
        run = app_state.run_state(self.root, self.platform)
        self.assertEqual((run['state'], run['phase'], run['pid']), ('failed', 'build', os.getpid()))
        self.assertIn('error[E0432]', run['output'])
        self.assertNotIn('hunter2', run['output'])


class EvidenceTest(unittest.TestCase):
    LISTED = {'domains': {
        'finance': {'name': 'Finance', 'description': 'Books.', 'repository': 'https://github.com/BelimbingProject/bilibi-dom-finance',
                    'versions': {'0.1.0': {'commit': 'f' * 40, 'platform': '>=0.1.0, <0.2.0', 'released': '2026-10-18'}}},
        'people': {'name': 'People', 'repository': 'https://github.com/BelimbingProject/bilibi-dom-people', 'versions': {}}},
        'tested': [{'platform': {'commit': 'a' * 40}, 'domain': {'id': 'finance', 'commit': 'f' * 40},
                    'tested_at': '2026-10-18T16:33:51Z', 'run': 'https://example.test/run'}]}

    def state(self, platform, finance, in_step=True):
        return {'components': [
            {'id': 'platform', 'kind': 'platform', 'mounted': True, 'commit': platform, 'saved': True, 'matches_saved': in_step},
            {'id': 'finance', 'kind': 'domains', 'mounted': True, 'commit': finance, 'saved': True, 'matches_saved': in_step}]}

    def evidence(self, *args, version='0.1.0', checked=False):
        entries = app_state.catalogue_entries(self.LISTED, self.state(*args), version, checked)
        return next(entry for entry in entries if entry['id'] == 'finance')['evidence']

    def test_requirement_ranges(self):
        self.assertTrue(app_state.satisfies('0.1.5', '>=0.1.0, <0.2.0'))
        self.assertFalse(app_state.satisfies('0.2.1', '>=0.1.0, <0.2.0'))
        self.assertIsNone(app_state.satisfies(None, '>=0.1.0'))
        self.assertIsNone(app_state.satisfies('0.2.1', 'whatever'))

    def test_three_kinds_of_evidence_stay_apart(self):
        tested = self.evidence('a' * 40, 'f' * 40)
        self.assertTrue(tested['exact_pair_tested_by_platform_ci']['tested'])
        self.assertTrue(tested['declared_version_support']['supported'])
        self.assertFalse(tested['whole_application_checked_here'])
        declared = self.evidence('b' * 40, 'f' * 40)
        self.assertFalse(declared['exact_pair_tested_by_platform_ci']['tested'])
        self.assertIn('declares support', declared['summary'])
        refused = self.evidence('b' * 40, 'f' * 40, version='0.2.1')
        self.assertIs(refused['declared_version_support']['supported'], False)
        self.assertIn('does not support', refused['summary'])
        self.assertIn('passed ./bilibi check', self.evidence('b' * 40, 'e' * 40, checked=True)['summary'])

    def test_unknown_is_never_called_compatible(self):
        unknown = self.evidence('b' * 40, 'e' * 40)
        self.assertIsNone(unknown['declared_version_support']['supported'])
        self.assertTrue(unknown['summary'].startswith('Unknown:'))
        self.assertFalse(self.evidence('b' * 40, 'e' * 40, False, checked=True)['whole_application_checked_here'])
        entries = app_state.catalogue_entries(self.LISTED, self.state('a' * 40, 'f' * 40), '0.1.0', False)
        people = next(entry for entry in entries if entry['id'] == 'people')
        self.assertEqual((people['added'], 'evidence' in people), (False, False))


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, body):
        script = self.root / 'bilibi'
        script.write_text('#!/bin/sh\n' + body + '\n')
        script.chmod(0o755)

    def finished(self, job):
        return jobs.wait(self.root, job['id'], 20)

    def test_outcome_and_sanitized_log_survive_the_launcher(self):
        self.command('echo "args: $*"; echo "job $BILIBI_JOB_ID prompt $GIT_TERMINAL_PROMPT"; '
                     'echo "token=ghp_0123456789abcdefghijklmnopqrstuvwxyzAB"; exit 3')
        job = self.finished(jobs.launch(self.root, ['check', '--yes']))
        self.assertEqual((job['state'], job['exit_code'], job['command']), ('failed', 3, ['check', '--yes']))
        log = jobs.log(self.root, job['id'])
        self.assertIn('args: check --yes', log)
        self.assertIn(f'job {job["id"]} prompt 0', log)
        self.assertNotIn('ghp_0123456789', log)
        self.assertEqual(jobs.listing(self.root)[0]['id'], job['id'])
        self.assertIsNone(jobs.read(self.root, '../outside'))

    def test_one_changing_job_at_a_time_and_runs_work_beside_it(self):
        self.command('sleep 3')
        first = jobs.launch(self.root, ['pin', '--yes'])
        with self.assertRaisesRegex(ValueError, f'Job {first["id"]} .* is still running'):
            jobs.launch(self.root, ['add', 'finance'])
        beside = jobs.launch(self.root, ['start'], kind='run')
        self.assertEqual(self.finished(first)['state'], 'succeeded')
        self.assertEqual(self.finished(beside)['state'], 'succeeded')
        self.assertEqual(self.finished(jobs.launch(self.root, ['add', 'finance']))['state'], 'succeeded')

    def test_a_vanished_runner_is_interrupted_not_guessed(self):
        folder = jobs.folder(self.root)
        folder.mkdir(parents=True)
        app_state.write_json(folder / '20260101-000000-abc123.json', {
            'id': '20260101-000000-abc123', 'kind': 'change', 'command': ['check'], 'started_at': 1.0,
            'finished_at': None, 'exit_code': None, 'runner': {'pid': os.getpid(), 'start': 1}})
        job = jobs.read(self.root, '20260101-000000-abc123')
        self.assertEqual(job['state'], 'interrupted')
        self.assertIn('outcome is uncertain', job['detail'])

    def test_a_stopped_run_is_not_a_failure(self):
        self.command('kill -TERM $$')
        job = self.finished(jobs.launch(self.root, ['start'], kind='run'))
        self.assertEqual((job['state'], job['exit_code']), ('stopped', 143))


class InsightTest(unittest.TestCase):
    def test_unavailable_figures_say_why_and_carry_a_time(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {'DATABASE_URL': ''}):
            root = Path(temp)
            measured = insight.measure(root, root / 'sources/platform', root / 'build')
        self.assertRegex(measured['measured_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d')
        for figure in (measured['cpu_percent'], measured['memory_bytes'], measured['database_bytes'],
                       measured['disk_bytes']['sources'], measured['disk_bytes']['build']):
            self.assertNotIn('value', figure)
            self.assertTrue(figure['unavailable'])
        self.assertIn('Memory: unavailable:', insight.render(measured))

    def test_measures_a_running_process_and_real_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            platform = root / 'sources/platform'
            (platform / '.local/dev').mkdir(parents=True)
            (root / 'build').mkdir()
            (root / 'build/output').write_bytes(b'x' * 100_000)
            process = subprocess.Popen(['sleep', '30'])
            self.addCleanup(lambda: (process.kill(), process.wait()))
            (platform / '.local/dev/app.pid').write_text(f'{process.pid}\n')
            measured = insight.measure(root, platform, root / 'build')
        self.assertEqual(measured['pid'], process.pid)
        self.assertGreater(measured['memory_bytes']['value'], 0)
        self.assertGreaterEqual(measured['cpu_percent']['value'], 0)
        self.assertGreaterEqual(measured['disk_bytes']['build']['value'], 100_000)
        self.assertIn('MB', insight.render(measured))


if __name__ == '__main__':
    unittest.main()
