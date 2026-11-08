"""Exercise the public interface with fake tools; never access a deployment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(REPOSITORY / 'scripts', self.root / 'scripts')
        (self.root / 'tests').mkdir()
        (self.root / 'tests/dispatch.sh').write_text('exit 0\n')
        self.local = self.root / 'environments/local'
        self.aws = self.root / 'environments/aws/dev'
        for target in (self.local, self.aws):
            (target / 'scripts').mkdir(parents=True)
            for script in (REPOSITORY / target.relative_to(self.root) / 'scripts').glob('*.sh'):
                shutil.copy2(script, target / 'scripts' / script.name)
            (target / 'scripts/validate.sh').write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$0" >> "$LOG"\n')
        (self.aws / 'config').mkdir()
        (self.aws / 'config/operator.tfvars').touch()
        (self.local / 'platform').mkdir()
        (self.local / 'platform/terraform.tfstate').touch()
        self.log = self.root / 'calls'
        self.log.touch()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'fake'
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = ' '.join(sys.argv[1:])
with open(os.environ['LOG'], 'a') as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
case = os.environ.get('CASE', '')
if name == 'kind': print('deployment-control-plane')
if name == 'kubectl':
    if 'config view' in args: print('wrong' if case == 'context' else 'https://eks.example')
    if 'get nodes' in args: print('wrong' if case == 'context' else 'deployment-control-plane')
if name == 'terraform':
    assert os.environ.get('TF_WORKSPACE') == 'default'
    assert not any(k.startswith('TF_CLI_ARGS') for k in os.environ)
    assert 'TF_DATA_DIR' not in os.environ
    if 'output' in args: print('kind-other' if case == 'state' else 'kind-deployment')
    if 'state list' in args and 'local' in args: print('helm_release.argocd\\nhelm_release.traefik')
if name == 'aws':
    if 'get-caller-identity' in args: print('wrong' if case == 'account' else '245361883993')
    if 'describe-cluster' in args: print('https://eks.example')
    if 'list-pod-identity-associations' in args: print('association')
    if 'describe-load-balancers' in args: print('arn:alb')
    if 'describe-target-groups' in args: print('arn:target')
    if 'describe-target-health' in args: print('1')
''')
        fake.chmod(0o755)
        for name in ('aws', 'terraform', 'kubectl', 'kind'):
            (self.bin / name).symlink_to(fake)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'], LOG=str(self.log),
                        TF_CLI_ARGS_init='-backend-config=wrong', TF_DATA_DIR='/wrong', TF_WORKSPACE='wrong')

    def run_command(self, *args, script='plan.sh', case=''):
        result = subprocess.run(['bash', str(self.root / 'scripts' / script), *args],
                                env=dict(self.env, CASE=case), text=True, capture_output=True)
        return result, self.log.read_text()

    def test_invalid_selection_never_invokes_tools(self):
        for args in ([], ['--all'], ['--environment', 'aws/prod', '--stage', 'foundation'],
                     ['--environment', 'local', '--stage', 'edge'],
                     ['--environment', 'aws/dev', '--stage', 'bootstrap'],
                     ['--environment', 'local', '--stage', 'platform'],
                     ['--environment', 'local', '--stage', 'platform', '--context', 'aws-selected'],
                     ['--environment', 'aws/dev', '--stage', 'platform']):
            with self.subTest(args=args):
                result, calls = self.run_command(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, '')

    def test_local_plans_only_local_state_without_aws(self):
        result, calls = self.run_command('--environment', 'local', '--stage', 'platform',
                                         '--context', 'kind-deployment', '--kubeconfig', '/tmp/selected file')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('["aws"', calls)
        self.assertNotIn(str(self.aws), calls)
        self.assertIn(str(self.local / 'platform'), calls)
        self.assertIn('--kubeconfig=/tmp/selected file', calls)
        self.assertIn('"plan"', calls)
        self.assertNotIn('"apply"', calls)

    def test_wrong_local_context_or_state_stops_before_init(self):
        for case in ('context', 'state'):
            self.log.write_text('')
            result, calls = self.run_command('--environment', 'local', '--stage', 'platform',
                                             '--context', 'kind-deployment', case=case)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('"init"', calls)

    def test_aws_scopes_each_plan_and_keeps_foundation_independent_of_kubernetes(self):
        for stage in ('foundation', 'platform', 'edge'):
            self.log.write_text('')
            result, calls = self.run_command('--environment', 'aws/dev', '--stage', stage,
                                             '--context', 'selected', '--kubeconfig', '/tmp/selected file')
            self.assertEqual(result.returncode, 0, result.stderr)
            plans = [json.loads(line) for line in calls.splitlines() if '"plan"' in line]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0][1], '-chdir=' + str(self.aws / stage))
            self.assertNotIn(str(self.local), calls)
            self.assertNotIn('"apply"', calls)
            self.assertNotIn('bootstrap', calls)
            if stage == 'foundation':
                self.assertNotIn('["kubectl"', calls)
                self.assertNotIn('describe-load-balancers', calls)

    def test_foundation_does_not_require_kubeconfig(self):
        self.env['KUBECONFIG'] = '/one:/two'
        result, calls = self.run_command('--environment', 'aws/dev', '--stage', 'foundation')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('["kubectl"', calls)

    def test_wrong_aws_account_or_context_stops_before_terraform(self):
        for case in ('account', 'context'):
            self.log.write_text('')
            result, calls = self.run_command('--environment', 'aws/dev', '--stage', 'platform',
                                             '--context', 'selected', case=case)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('["terraform"', calls)

    def test_validation_dispatches_only_requested_environments(self):
        for environment, target in (('local', self.local), ('aws/dev', self.aws)):
            self.log.write_text('')
            result, calls = self.run_command('--environment', environment, script='validate.sh')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.strip(), str(target / 'scripts/validate.sh'))
        self.log.write_text('')
        result, calls = self.run_command('--all', script='validate.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls.splitlines()), 2)
        self.log.write_text('')
        result, calls = self.run_command('--all', '--environment', 'local', script='validate.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')

    def test_backend_contracts_and_local_provider_isolation(self):
        for stage, key in (('foundation', 'dev/terraform.tfstate'),
                           ('platform', 'dev/platform/terraform.tfstate'), ('edge', 'dev/edge/terraform.tfstate')):
            backend = (REPOSITORY / 'environments/aws/dev' / stage / 'backend.tf').read_text()
            self.assertIn('"' + key + '"', backend)
            self.assertIn('allowed_account_ids = ["245361883993"]', backend)
        self.assertIn('"bootstrap/terraform.tfstate"', (REPOSITORY / 'bootstrap/aws/backend.tf').read_text())
        local = '\n'.join(p.read_text() for p in (REPOSITORY / 'environments/local/platform').glob('*.tf'))
        self.assertNotIn('hashicorp/aws', local)
        self.assertNotIn('backend "s3"', local)
        self.assertEqual(local.count('prevent_destroy = true'), 2)
