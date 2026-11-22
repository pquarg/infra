"""Guard recovery and credential preservation for the deployment warehouse."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reaction_database', Path(__file__).resolve().parents[1] / 'scripts/reaction-explorer-database.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ReactionDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer = self.root / 'writer'
        self.reader = self.root / 'reader'
        for path in (self.writer, self.reader):
            path.write_text(path.name + "'private")
            path.chmod(0o600)
        self.calls = []
        self.populated = False
        self.fail_restore = False

    def run_command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        data = kwargs.get('input', b'')
        code, output = 0, b''
        if command[0] == 'git':
            code = 1
        elif 'psql' in command:
            if b'pg_get_userbyid' in data:
                output = b'postgres'
            elif b'current_user ||' in data:
                output = (b'dashboard' if command[command.index('-U') + 1] == 'dashboard' else b'warehouse') + b':reaction_warehouse'
            elif b'pg_depend' in data:
                output = b'1' if self.populated else b'0'
            elif b'SHOW default_transaction_read_only' in data:
                output = b'on'
            else:
                output = b'f'
        elif 'pg_restore' in command and self.fail_restore:
            code = 1
        return subprocess.CompletedProcess(command, code, stdout=output, stderr=b'private error')

    def invoke(self, action, path=None):
        argv = ['helper', '--environment', 'local', '--password-file', str(self.writer),
                '--dashboard-password-file', str(self.reader), action]
        if path:
            argv.append(str(path))
        with patch.object(sys, 'argv', argv), patch.object(helper.subprocess, 'run', self.run_command):
            helper.main()

    def test_provision_retains_existing_passwords_and_uses_stdin(self):
        self.invoke('provision')
        sql = b'\n'.join(k.get('input', b'') for _, k in self.calls)
        self.assertNotIn(b'ALTER ROLE warehouse', sql)
        self.assertNotIn(b'PASSWORD', sql.split(b'ALTER ROLE dashboard')[1])
        self.assertNotIn(b'DROP DATABASE', sql)
        self.assertIn(b'ALTER DEFAULT PRIVILEGES FOR ROLE warehouse', sql)
        for command, _ in self.calls:
            self.assertNotIn(self.writer.read_text(), ' '.join(command))
            self.assertNotIn(self.reader.read_text(), ' '.join(command))

    def test_populated_warehouse_refuses_restore(self):
        self.populated = True
        archive = self.root / 'archive.dump'
        archive.write_bytes(b'archive')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            self.invoke('restore', archive)
        self.assertFalse(any('pg_restore' in c for c, _ in self.calls))

    def test_failed_verification_removes_only_temporary_database(self):
        self.fail_restore = True
        archive = self.root / 'archive.dump'
        archive.write_bytes(b'archive')
        with self.assertRaises(RuntimeError):
            self.invoke('verify-backup', archive)
        drops = [k['input'] for _, k in self.calls if b'DROP DATABASE' in k.get('input', b'')]
        self.assertEqual(len(drops), 1)
        self.assertIn(b'DROP DATABASE rx_restore_check_', drops[0])

    def test_restore_is_transactional_and_excludes_other_schemas(self):
        archive = self.root / 'archive.dump'
        archive.write_bytes(b'archive')
        self.invoke('restore', archive)
        restore = next(c for c, _ in self.calls if 'pg_restore' in c)
        self.assertIn('--schema=public', restore)
        self.assertIn('--schema=analytics', restore)
        self.assertTrue(any('--single-transaction' in c for c, _ in self.calls))

    def test_public_password_file_is_rejected(self):
        self.reader.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'chmod 600'):
            self.invoke('check')
