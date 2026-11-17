"""Guard the tracker helper against credential resets and destructive recovery."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'tracker_database', Path(__file__).resolve().parents[1] / 'scripts/job-tracker-database.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class TrackerDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.password = self.root / 'password'
        self.password.write_text("private'quoted-password")
        self.password.chmod(0o600)
        self.calls = []
        self.populated = False
        self.fail_restore = False
        self.fail_dump = False
        self.owner = 'postgres'

    def run_command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        data = kwargs.get('input', b'')
        code, output = 0, b''
        if command[0] == 'git':
            code = 1
        elif 'pg_dump' in command:
            code, output = (1 if self.fail_dump else 0), b'archive'
        elif 'pg_restore' in command:
            code = int(self.fail_restore and '--file=/dev/null' not in command)
        elif 'psql' in command:
            if b'pg_get_userbyid' in data:
                output = self.owner.encode()
            elif b'current_user ||' in data:
                output = b'job_tracker:job_tracker'
            elif b'has_database_privilege' in data:
                output = b'f'
            elif b'has_table_privilege' in data:
                output = b'0'
            elif b'SELECT rolsuper' in data:
                output = b'f'
            elif b'SELECT count(*) FROM pg_depend' in data:
                output = b'1' if self.populated else b'0'
            elif b'SELECT version_num' in data:
                output = b'e7b13a920ac4'
        return subprocess.CompletedProcess(command, code, stdout=output, stderr=b'private error')

    def invoke(self, action, path=None):
        argv = ['helper', '--environment', 'local', '--password-file', str(self.password), action]
        if path:
            argv.append(str(path))
        with patch.object(sys, 'argv', argv), patch.object(helper.subprocess, 'run', self.run_command):
            helper.main()

    def test_provision_is_additive_and_password_never_in_process_arguments(self):
        self.invoke('provision')
        sql = b'\n'.join(k.get('input', b'') for _, k in self.calls)
        self.assertIn(b'WHERE NOT EXISTS', sql)
        self.assertNotIn(b'ALTER ROLE', sql)
        self.assertNotIn(b'DROP DATABASE', sql)
        self.assertNotIn(b'ALTER DATABASE myfastapiapp', sql)
        for command, _ in self.calls:
            self.assertNotIn(self.password.read_text(), ' '.join(command))

    def test_wrong_database_owner_stops_before_grants(self):
        self.owner = 'someone_else'
        with self.assertRaises(ValueError):
            self.invoke('provision')
        self.assertFalse(any(b'REVOKE ALL' in k.get('input', b'') for _, k in self.calls))

    def test_populated_schema_refuses_restore(self):
        archive = self.root / 'backup.dump'
        archive.write_bytes(b'archive')
        self.populated = True
        with self.assertRaisesRegex(ValueError, 'not empty'):
            self.invoke('restore', archive)
        self.assertFalse(any('pg_restore' in c for c, _ in self.calls))

    def test_verification_failure_drops_only_scratch_database(self):
        archive = self.root / 'backup.dump'
        archive.write_bytes(b'archive')
        self.fail_restore = True
        with self.assertRaisesRegex(RuntimeError, 'operation failed'):
            self.invoke('verify-backup', archive)
        drops = [k['input'] for _, k in self.calls if b'DROP DATABASE' in k.get('input', b'')]
        self.assertEqual(len(drops), 1)
        self.assertIn(b'DROP DATABASE jt_restore_check_', drops[0])

    def test_failed_backup_leaves_no_successful_or_partial_archive(self):
        self.fail_dump = True
        destination = self.root / 'backups'
        destination.mkdir()
        with self.assertRaises(RuntimeError):
            self.invoke('backup', destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_insecure_password_permissions_refused(self):
        self.password.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'chmod 600'):
            self.invoke('check')

    def test_restore_clears_only_restored_chatgpt_grants(self):
        archive = self.root / 'backup.dump'
        archive.write_bytes(b'archive')
        self.invoke('restore', archive)
        resets = [(c, k['input']) for c, k in self.calls
                  if b'UPDATE public.chatgpt_connections' in k.get('input', b'')]
        self.assertEqual(len(resets), 1)
        command, sql = resets[0]
        self.assertEqual(command[command.index('-d') + 1], 'job_tracker')
        self.assertIn(b'credentials = NULL', sql)
        self.assertIn(b'DELETE FROM public.chatgpt_authorizations', sql)
        self.assertNotIn(b'DELETE FROM public.users', sql)
        restore_at = next(i for i, (c, _) in enumerate(self.calls) if 'pg_restore' in c)
        reset_at = next(i for i, (_, k) in enumerate(self.calls)
                        if b'UPDATE public.chatgpt_connections' in k.get('input', b''))
        self.assertLess(restore_at, reset_at)

    def test_verification_resets_credentials_only_in_scratch(self):
        archive = self.root / 'backup.dump'
        archive.write_bytes(b'archive')
        self.invoke('verify-backup', archive)
        resets = [c for c, k in self.calls
                  if b'UPDATE public.chatgpt_connections' in k.get('input', b'')]
        self.assertEqual(len(resets), 1)
        self.assertTrue(resets[0][resets[0].index('-d') + 1].startswith('jt_restore_check_'))
