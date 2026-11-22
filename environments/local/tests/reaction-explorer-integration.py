"""Exercise only a disposable PostgreSQL Compose project; never the deployment DB.

Run explicitly with Docker Desktop: python3 tests/reaction-explorer-integration.py
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

source = Path(__file__).resolve().parents[1] / 'scripts/reaction-explorer-database.py'
spec = importlib.util.spec_from_file_location('helper', source)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    (root/'database').mkdir()
    (root/'database/.env').write_text('')
    (root/'database/compose.yaml').write_text('''name: rx-test-'''+uuid.uuid4().hex[:10]+'''
services:
  postgres:
    image: postgres:17.11-bookworm
    environment:
      POSTGRES_PASSWORD: disposable-fixture-admin
    healthcheck:
      test: [CMD-SHELL, "pg_isready -U postgres"]
      interval: 1s
      retries: 60
''')
    compose=['docker','compose','-f',str(root/'database/compose.yaml')]
    h.ROOT=root
    args=['helper','--environment','local','--password-file',str(root/'writer'),'--dashboard-password-file',str(root/'reader')]
    def run(action,*more):
        sys.argv=args+[action,*map(str,more)]
        h.main()
    def admin(sql):
        subprocess.run(compose+['exec','-T','postgres','psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','reaction_warehouse'],input=sql,text=True,check=True,stdout=subprocess.DEVNULL)
    try:
        subprocess.run(compose+['up','-d','--wait'],check=True)
        run('provision')
        run('provision')
        admin('SET ROLE warehouse; CREATE TABLE public.experiment(id integer); INSERT INTO public.experiment VALUES (1); CREATE TABLE analytics.solvent_comparisons(id integer); INSERT INTO analytics.solvent_comparisons VALUES (1);')
        run('check')
        (root/'backups').mkdir()
        run('backup',root/'backups')
        archive=next((root/'backups').glob('*.dump'))
        run('verify-backup',archive)
        try:
            run('restore',archive)
        except ValueError:
            pass
        else:
            raise AssertionError('Populated restore accepted')
        admin('SET ROLE warehouse; DROP TABLE public.experiment; DROP TABLE analytics.solvent_comparisons;')
        run('restore',archive)
        run('provision')
        run('check')
        print('Disposable PostgreSQL provision/retry, grants, backup, verification and empty-only restore passed.')
    finally:
        subprocess.run(compose+['down','--volumes'],check=True)
