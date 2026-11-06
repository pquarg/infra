#!/usr/bin/env python3
"""Build the local platform and deploy myfastapiapp without replacing existing data."""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
GITOPS = WORKSPACE / 'gitops'
APP = WORKSPACE / 'apps/myfastapiapp'
CONTEXT = 'kind-argocd-postgres'
VOLUME = 'deployment-project-myfastapiapp-postgres'
REPOSITORY = 'git@github.com:pquarg/gitops.git'


class SetupError(Exception):
    pass


def capture(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        # Some tools echo Secret input in validation errors. Never relay captured
        # output, even when a command fails. Arguments contain no passwords.
        raise SetupError(f'Command failed: {shlex.join(map(str, command))}')
    return result.stdout.strip()


def run(command):
    subprocess.run(command, check=True)


def stage(number, description):
    print(f'\n[{number}/8] {description}', flush=True)


def private_file(path, content):
    """Exclusive creation: reruns never overwrite a credential or local setting."""
    with open(path, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
        output.write(content)


def render(path):
    stream = capture(['kubectl', 'patch', '--local', '-k', str(path),
                      '--type=merge', '-p', '{}', '-o', 'json'])
    decoder = json.JSONDecoder()
    resources = []
    while stream:
        resource, end = decoder.raw_decode(stream)
        resources.extend(resource['items'] if resource['kind'] == 'List' else [resource])
        stream = stream[end:].lstrip()
    return resources


def published_gitops():
    paths = ['apps/myfastapiapp', 'argocd/local', 'root/staging.yaml']
    if capture(['git', '-C', str(GITOPS), 'status', '--porcelain', '--', *paths]):
        raise SetupError('Publish local GitOps manifest changes first; Argo CD reads GitHub main.')
    remote = capture(['git', '-C', str(GITOPS), 'ls-remote', 'origin', 'refs/heads/main']).split()
    head = capture(['git', '-C', str(GITOPS), 'rev-parse', 'HEAD'])
    if not remote or remote[0] != head:
        raise SetupError('The GitOps checkout must match origin/main. Review and synchronize it first.')
    root = json.loads(capture(['kubectl', 'patch', '--local', '-f', str(GITOPS / 'root/staging.yaml'),
                              '--type=merge', '-p', '{}', '-o', 'json']))
    if root['spec']['source']['repoURL'] != REPOSITORY:
        raise SetupError('This setup targets pquarg/gitops. Follow manual setup for another remote.')
    return head


def selected_image():
    resources = render(GITOPS / 'apps/myfastapiapp/overlays/local')
    deployment = next(r for r in resources if r['kind'] == 'Deployment')
    job = next(r for r in resources if r['kind'] == 'Job')
    image = deployment['spec']['template']['spec']['containers'][0]['image']
    if image != job['spec']['template']['spec']['containers'][0]['image']:
        raise SetupError('API and migration images differ.')
    if capture(['git', '-C', str(APP), 'status', '--porcelain']):
        raise SetupError('Commit app source changes and select a new release before building.')
    version = tomllib.loads((APP / 'pyproject.toml').read_text())['project']['version']
    if image != f'ghcr.io/pquarg/myfastapiapp:{version}':
        raise SetupError('The source version must match the local overlay image tag before building.')
    return image


def existing_cluster(kube):
    if 'argocd-postgres' not in capture(['kind', 'get', 'clusters']).splitlines():
        return False
    nodes = capture(['kind', 'get', 'nodes', '--name', 'argocd-postgres']).splitlines()
    actual = capture(kube + ['get', 'nodes', '-o', 'json'])
    if sorted(nodes) != sorted(item['metadata']['name'] for item in json.loads(actual)['items']):
        raise SetupError('Selected kubeconfig does not point to the expected kind nodes.')
    legacy = json.loads(capture(kube + ['-n', 'myfastapiapp', 'get', 'statefulset',
                                      'myfastapiapp-postgres-statefulset', '--ignore-not-found', '-o', 'json']) or '{}')
    if legacy and legacy.get('spec', {}).get('replicas', 1) != 0:
        raise SetupError('A legacy PostgreSQL workload is active. Follow gitops/docs/local/database-migration.md.')
    for kind, name, namespace in [('pvc', 'myfastapiapp-postgres-pvc', 'default'),
                                  ('application', 'myfastapiapp-postgres-application', 'argocd'),
                                  ('application', 'staging-bootstrap', 'argocd')]:
        # Applications cannot exist before Argo CD's CRD has been installed.
        if kind == 'application' and not capture(kube + ['get', 'crd', 'applications.argoproj.io', '--ignore-not-found', '-o', 'name']):
            continue
        if capture(kube + ['-n', namespace, 'get', kind, name, '--ignore-not-found', '-o', 'name']):
            raise SetupError('Legacy app registration/storage needs the documented migration first.')
    return True


def database_settings():
    env = ROOT / 'database/.env'
    has_volume = VOLUME in capture(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
    if not env.exists():
        if has_volume:
            raise SetupError('Existing database volume has no local .env. Recover its original settings/passwords first.')
        directory = Path.home() / '.local/share/infra-local/database-secrets'
        # Compose dotenv single quotes keep spaces and dollar signs literal.
        value = str(directory).replace("'", "\\'")
        private_file(env, f"LOCAL_DB_SECRETS_DIR='{value}'\nLOCAL_DB_PORT=5433\n")
    config = json.loads(capture(['docker', 'compose', '--env-file', str(env), '-f',
                                 str(ROOT / 'database/compose.yaml'), 'config', '--format', 'json']))
    service = config['services']['postgres']
    ports = service['ports']
    if len(ports) != 1 or ports[0].get('host_ip') != '127.0.0.1' or ports[0]['target'] != 5432:
        raise SetupError('Expected one loopback-only PostgreSQL port; review database/.env and compose.yaml.')
    port = int(ports[0]['published'])
    if not 1 <= port <= 65535:
        raise SetupError('Set a fixed LOCAL_DB_PORT between 1 and 65535.')
    files = [Path(config['secrets'][name]['file']) for name in ('admin_password', 'app_password')]
    for path in files:
        if path.resolve().is_relative_to(WORKSPACE.resolve()):
            raise SetupError('Database passwords must be outside the workspace.')
        if not path.exists():
            if has_volume:
                raise SetupError('Password file missing for an existing volume. Recover it; do not generate a new password.')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_file(path, secrets.token_hex(24) + '\n')
        if not path.read_text().rstrip('\r\n'):
            raise SetupError('Database password files must not be empty.')
    return port, files[1]


def get_secret(kube, namespace, name):
    return json.loads(capture(kube + ['-n', namespace, 'get', 'secret', name,
                                    '--ignore-not-found', '-o', 'json']) or '{}')


def decoded(secret, name):
    return base64.b64decode(secret.get('data', {}).get(name, '')).decode()


def create_secret(kube, namespace, name, values, labels=None, secret_type='Opaque'):
    resource = {'apiVersion': 'v1', 'kind': 'Secret', 'type': secret_type,
                'metadata': {'name': name, 'namespace': namespace, 'labels': labels or {}},
                'stringData': values}
    result = subprocess.run(kube + ['create', '-f', '-'], input=json.dumps(resource),
                            capture_output=True, text=True)
    if result.returncode:
        raise SetupError(f'Could not create {namespace}/{name}; inspect access and existing resources. Secret output suppressed.')


def app_credentials(kube, port, password_file):
    if not capture(kube + ['get', 'namespace', 'myfastapiapp', '--ignore-not-found', '-o', 'name']):
        run(kube + ['create', 'namespace', 'myfastapiapp'])
    password = quote(password_file.read_text().rstrip('\r\n'), safe='')
    url = f'postgresql://myfastapiapp:{password}@host.docker.internal:{port}/myfastapiapp?sslmode=disable'
    existing = get_secret(kube, 'myfastapiapp', 'myfastapiapp-secrets')
    if existing:
        if decoded(existing, 'AUTH_DATABASE_URL') != url:
            raise SetupError('Existing app connection Secret differs. Review migration/rotation manually; it was not overwritten.')
        print('Reusing the existing app connection Secret.')
    else:
        create_secret(kube, 'myfastapiapp', 'myfastapiapp-secrets', {'AUTH_DATABASE_URL': url})
    if not get_secret(kube, 'myfastapiapp', 'registry-credentials'):
        # Workloads reference this Secret even with an image loaded into kind.
        # An empty auth config avoids missing-Secret events; it grants no access
        # to private packages. Existing real registry credentials are preserved.
        create_secret(kube, 'myfastapiapp', 'registry-credentials',
                      {'.dockerconfigjson': json.dumps({'auths': {}})},
                      secret_type='kubernetes.io/dockerconfigjson')


def git_credentials(kube, key_path):
    existing = json.loads(capture(kube + ['-n', 'argocd', 'get', 'secrets', '-l',
                                        'argocd.argoproj.io/secret-type in (repository,repo-creds)', '-o', 'json']))
    for secret in existing['items']:
        url = decoded(secret, 'url')
        template = secret['metadata']['labels']['argocd.argoproj.io/secret-type'] == 'repo-creds'
        if url and (REPOSITORY.startswith(url) if template else url == REPOSITORY):
            if decoded(secret, 'project') in ('', 'default'):
                print('Reusing existing Argo CD Git credentials; app sync will verify access.')
                return
    if not key_path:
        print('Argo CD needs a read-only deploy key authorized on GitHub for pquarg/gitops.')
        print('See docs/setup-local.md for key creation and GitHub authorization.')
        key_path = input('Path to the unencrypted SSH private key: ').strip()
    path = Path(key_path).expanduser()
    # A passphrase prompt cannot be serviced by Argo CD. Validate without echoing
    # either the public or private key and without changing host verification.
    result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(path)], capture_output=True)
    if result.returncode:
        raise SetupError('SSH key is unreadable or encrypted; Argo CD needs an unencrypted deploy key.')
    create_secret(kube, 'argocd', 'local-setup-gitops',
                  {'type': 'git', 'url': REPOSITORY, 'project': 'default', 'sshPrivateKey': path.read_text()},
                  {'argocd.argoproj.io/secret-type': 'repository'})


def wait_for_app(kube, revision, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app = json.loads(capture(kube + ['-n', 'argocd', 'get', 'application', 'staging-myfastapiapp',
                                       '--ignore-not-found', '-o', 'json']) or '{}')
        status = app.get('status', {})
        sync = status.get('sync', {})
        if (sync.get('status') == 'Synced' and sync.get('revision') == revision
                and status.get('health', {}).get('status') == 'Healthy'
                and status.get('operationState', {}).get('phase') == 'Succeeded'):
            return
        time.sleep(5)
    raise SetupError('App did not become Synced/Healthy at the published revision within 10 minutes. Inspect Argo CD and migration logs; maintenance pauses are preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check prerequisites/configuration only; do not provision')
    parser.add_argument('--kubeconfig', default=os.environ.get('INFRA_LOCAL_KUBECONFIG', os.environ.get('KUBECONFIG', str(Path.home() / '.kube/config'))))
    parser.add_argument('--git-ssh-key', help='Existing unencrypted read-only GitHub deploy-key private file')
    args = parser.parse_args()
    if ':' in args.kubeconfig or not Path(args.kubeconfig).is_absolute():
        parser.error('--kubeconfig must select one absolute file')
    os.environ['KUBECONFIG'] = args.kubeconfig
    os.environ['INFRA_LOCAL_KUBECONFIG'] = args.kubeconfig
    kube = ['kubectl', '--kubeconfig', args.kubeconfig, '--context', CONTEXT, '--request-timeout=30s']
    try:
        stage(1, 'Check tools, published manifests and configuration')
        for name in ('docker', 'kind', 'kubectl', 'terraform', 'helm', 'kubeconform', 'git', 'curl', 'ssh-keygen'):
            if not shutil.which(name):
                raise SetupError(f'{name} is required. Install the documented prerequisites first.')
        capture(['docker', 'info'])
        capture(['docker', 'compose', 'version'])
        published_gitops()
        image = selected_image()
        exists = existing_cluster(kube)
        run([str(ROOT / 'scripts/validate.sh')])
        run([str(GITOPS / 'scripts/validate.sh'), '--ready'])
        if args.check:
            print('Checks passed. No database settings, credentials, volumes or cluster resources were created.')
            return 0
        if not sys.stdin.isatty():
            raise SetupError('Run setup in an interactive terminal for the Terraform review and credential prompt.')
        stage(2, 'Prepare/reuse private database settings and start PostgreSQL')
        port, password_file = database_settings()
        run([str(ROOT / 'scripts/local-database.sh'), 'up'])
        stage(3, 'Create/reuse kind and install Argo CD/Traefik with reviewed Terraform apply')
        if not exists:
            config = ROOT / 'kind/cluster.local.yaml'
            if not config.exists():
                private_file(config, (ROOT / 'kind/cluster.example.yaml').read_text())
            run([str(ROOT / 'scripts/create-cluster.sh')])
        run([str(ROOT / 'scripts/bootstrap-kind.sh')])
        stage(4, 'Provision/reuse the app connection and Argo CD Git credentials')
        app_credentials(kube, port, password_file)
        git_credentials(kube, args.git_ssh_key)
        stage(5, f'Build and load {image}')
        run(['docker', 'build', '--provenance=false', '-t', image, str(APP)])
        run(['kind', 'load', 'docker-image', image, '--name', 'argocd-postgres'])
        stage(6, 'Verify Pod-to-database connectivity and app privileges')
        run([sys.executable, str(GITOPS / 'scripts/check-local-database.py'), '--environment', 'local',
             '--context', CONTEXT, '--kubeconfig', args.kubeconfig])
        stage(7, 'Register local apps and wait for the published app revision')
        revision = published_gitops()
        run([str(GITOPS / 'scripts/bootstrap.sh'), '--environment', 'local', '--context', CONTEXT,
             '--kubeconfig', args.kubeconfig])
        wait_for_app(kube, revision)
        stage(8, 'Verify migrations, rollout and HTTP health/readiness')
        run(kube + ['-n', 'myfastapiapp', 'logs', 'job/myfastapiapp-migrate'])
        run(kube + ['-n', 'myfastapiapp', 'rollout', 'status', 'deployment/myfastapiapp-deployment', '--timeout=300s'])
        run(kube + ['-n', 'myfastapiapp', 'exec', 'deployment/myfastapiapp-deployment', '--',
                    'python', '-m', 'alembic', 'current', '--check-heads'])
        for endpoint in ('health', 'ready'):
            run(['curl', '--noproxy', '*', '--fail', '--silent', '--show-error', '--connect-timeout', '3',
                 '--max-time', '10', f'http://myfastapiapp.localhost/{endpoint}'])
        print('\nPlatform and application checks passed. Test registration and login at:')
        print('http://myfastapiapp.localhost/api/v1/docs/')
        print('For the optional Argo CD UI, run in another terminal:')
        print(shlex.join(kube + ['-n', 'argocd', 'port-forward', 'service/argocd-server', '8080:443']))
        return 0
    except (SetupError, OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        print(f'\nSetup stopped: {error}', file=sys.stderr)
        print('Completed resources are retained. Resolve the error and rerun; nothing is torn down.', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSetup interrupted. Completed resources and credentials are retained.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
