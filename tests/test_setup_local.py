"""Exercise setup sequencing and preservation without creating infrastructure."""
import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('setup_local', Path(__file__).resolve().parents[1] / 'scripts/setup-local.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_private_file_is_exclusive_and_mode_600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'password'
            setup.private_file(path, 'original')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                setup.private_file(path, 'replacement')
            self.assertEqual(path.read_text(), 'original')

    def test_existing_volume_never_gets_replacement_settings_or_passwords(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'database').mkdir()
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'capture', return_value=setup.VOLUME):
                with self.assertRaisesRegex(setup.SetupError, 'Recover its original'):
                    setup.database_settings()
                self.assertFalse((root / 'database/.env').exists())

    def database_fixture(self, root):
        (root / 'database').mkdir()
        (root / 'database/.env').write_text('existing settings')
        files = [root / 'passwords/admin', root / 'passwords/app']
        config = {'services': {'postgres': {'ports': [{'host_ip': '127.0.0.1', 'target': 5432, 'published': '5444'}]}},
                  'secrets': dict(zip(('admin_password', 'app_password'), ({'file': str(p)} for p in files)))}
        return files, config

    def test_fresh_passwords_are_generated_once_and_existing_port_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files, config = self.database_fixture(root)
            def capture(command):
                return '' if command[:3] == ['docker', 'volume', 'ls'] else json.dumps(config)
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'capture', side_effect=capture):
                self.assertEqual(setup.database_settings(), (5444, files[1]))
                original = [p.read_text() for p in files]
                self.assertEqual(setup.database_settings(), (5444, files[1]))
                self.assertEqual([p.read_text() for p in files], original)
                self.assertNotEqual(original[0], original[1])
            self.assertEqual((root / 'database/.env').read_text(), 'existing settings')

    def test_missing_password_for_existing_volume_stops_before_generating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files, config = self.database_fixture(root)
            def capture(command):
                return setup.VOLUME if command[:3] == ['docker', 'volume', 'ls'] else json.dumps(config)
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'capture', side_effect=capture):
                with self.assertRaisesRegex(setup.SetupError, 'Password file missing'):
                    setup.database_settings()
            self.assertFalse(any(p.exists() for p in files))

    def test_existing_app_secret_cannot_be_silently_switched(self):
        secret = {'data': {'AUTH_DATABASE_URL': base64.b64encode(b'old-secret-url').decode()}}
        with tempfile.TemporaryDirectory() as directory:
            password = Path(directory) / 'password'
            password.write_text('new-password')
            with patch.object(setup, 'capture', return_value='namespace/myfastapiapp'), \
                    patch.object(setup, 'get_secret', return_value=secret), \
                    patch.object(setup, 'create_secret') as create:
                with self.assertRaisesRegex(setup.SetupError, 'not overwritten'):
                    setup.app_credentials(['kubectl'], 5433, password)
                create.assert_not_called()

    def test_secret_error_does_not_echo_tool_output(self):
        result = subprocess.CompletedProcess([], 1, stdout='private-password', stderr='private-password')
        with patch.object(setup.subprocess, 'run', return_value=result):
            with self.assertRaises(setup.SetupError) as error:
                setup.create_secret(['kubectl'], 'ns', 'name', {'password': 'private-password'})
        self.assertNotIn('private-password', str(error.exception))

    def test_existing_git_credentials_are_reused(self):
        secret = {'metadata': {'labels': {'argocd.argoproj.io/secret-type': 'repository'}},
                  'data': {'url': base64.b64encode(setup.REPOSITORY.encode()).decode(),
                           'sshPrivateKey': base64.b64encode(b'private-key').decode()}}
        with patch.object(setup, 'capture', return_value=json.dumps({'items': [secret]})), \
                patch.object(setup, 'create_secret') as create:
            setup.git_credentials(['kubectl'], '/unneeded/key')
            create.assert_not_called()

    def test_legacy_running_database_stops_setup(self):
        responses = ['argocd-postgres', 'kind-node', json.dumps({'items': [{'metadata': {'name': 'kind-node'}}]}),
                     json.dumps({'spec': {'replicas': 1}})]
        with patch.object(setup, 'capture', side_effect=responses):
            with self.assertRaisesRegex(setup.SetupError, 'legacy PostgreSQL'):
                setup.existing_cluster(['kubectl', '--context', setup.CONTEXT])

    def test_app_wait_requires_current_revision_and_successful_sync(self):
        status = {'sync': {'status': 'Synced', 'revision': 'older'}, 'health': {'status': 'Healthy'},
                  'operationState': {'phase': 'Succeeded'}}
        current = dict(status, sync={'status': 'Synced', 'revision': 'expected'})
        with patch.object(setup, 'capture', side_effect=[json.dumps({'status': status}), json.dumps({'status': current})]), \
                patch.object(setup.time, 'sleep') as sleep:
            setup.wait_for_app(['kubectl'], 'expected')
            sleep.assert_called_once()

    def main_fixture(self, check=False):
        args = ['setup-local.py', '--kubeconfig', '/tmp/selected kubeconfig']
        if check:
            args.append('--check')
        patches = [patch.object(setup.sys, 'argv', args), patch.object(setup.sys.stdin, 'isatty', return_value=True),
                   patch.object(setup.shutil, 'which', return_value='/tool'), patch.object(setup, 'capture', return_value=''),
                   patch.object(setup, 'published_gitops', return_value='revision'),
                   patch.object(setup, 'selected_image', return_value='app:release'),
                   patch.object(setup, 'existing_cluster', return_value=True),
                   patch.object(setup, 'database_settings', return_value=(5433, Path('/private/password'))),
                   patch.object(setup, 'app_credentials'), patch.object(setup, 'git_credentials'),
                   patch.object(setup, 'wait_for_app'), patch.dict(setup.os.environ, {}, clear=False)]
        return patches

    def test_check_mode_does_not_provision_and_normal_mode_orders_checks_before_registration(self):
        for check in (True, False):
            with self.subTest(check=check), contextlib.ExitStack() as stack:
                for item in self.main_fixture(check):
                    stack.enter_context(item)
                run = stack.enter_context(patch.object(setup, 'run'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(setup.main(), 0)
                calls = [call.args[0] for call in run.call_args_list]
                if check:
                    self.assertEqual(len(calls), 2)  # Validators only.
                    setup.database_settings.assert_not_called()
                    setup.app_credentials.assert_not_called()
                else:
                    probe = next(i for i, c in enumerate(calls) if any('check-local-database.py' in arg for arg in c))
                    register = next(i for i, c in enumerate(calls) if c[0] == str(setup.GITOPS / 'scripts/bootstrap.sh'))
                    self.assertLess(probe, register)
                    self.assertFalse(any('create-cluster.sh' in str(c) for c in calls))
                    self.assertFalse(any('auto-approve' in arg or arg == 'destroy' for c in calls for arg in c))
                    for command in calls:
                        if command[0] == 'kubectl':
                            self.assertIn(setup.CONTEXT, command)
                            self.assertIn('/tmp/selected kubeconfig', command)

    def test_invalid_kubeconfig_is_rejected(self):
        with patch.object(setup.sys, 'argv', ['setup-local.py', '--kubeconfig', '/a:/b']), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                setup.main()
            self.assertEqual(error.exception.code, 2)

    def test_missing_cluster_preserves_existing_kind_config_or_copies_example(self):
        for configured in (False, True):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
                root = Path(directory)
                (root / 'kind').mkdir()
                example = root / 'kind/cluster.example.yaml'
                example.write_text('new cluster ports only')
                config = root / 'kind/cluster.local.yaml'
                if configured:
                    config.write_text('preserve my legacy mounts')
                for item in self.main_fixture():
                    stack.enter_context(item)
                setup.existing_cluster.return_value = False
                stack.enter_context(patch.object(setup, 'ROOT', root))
                run = stack.enter_context(patch.object(setup, 'run'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(setup.main(), 0)
                self.assertEqual(config.read_text(), 'preserve my legacy mounts' if configured else example.read_text())
                run.assert_any_call([str(root / 'scripts/create-cluster.sh')])


if __name__ == '__main__':
    unittest.main()
