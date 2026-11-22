#!/usr/bin/env python3
"""Provision and recover only reaction_warehouse on the existing local Compose server."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def outside_git(path):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError('Choose an absolute private path')
    path = path.resolve()
    parent = path if path.is_dir() else path.parent
    if not path.is_absolute() or not parent.is_dir():
        raise ValueError('Choose an existing absolute private directory')
    if subprocess.run(['git', '-C', str(parent), 'rev-parse', '--is-inside-work-tree'],
                      capture_output=True).returncode == 0:
        raise ValueError('Credentials and backups must be outside Git working trees')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True, choices=['local'])
    parser.add_argument('--password-file', required=True)
    parser.add_argument('--dashboard-password-file', required=True)
    parser.add_argument('action', choices=['provision', 'check', 'backup', 'verify-backup', 'restore'])
    parser.add_argument('path', nargs='?', help='Backup directory or archive (outside Git)')
    args = parser.parse_args()
    if (args.action in {'backup', 'verify-backup', 'restore'}) != bool(args.path):
        parser.error('backup needs a directory; verify-backup/restore need an archive')
    def credential(filename):
        path = outside_git(filename)
        if not path.exists() and args.action == 'provision':
            with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
                file.write(secrets.token_hex(32))
        if path.stat().st_mode & 0o077:
            raise ValueError('Password file must be private (chmod 600)')
        value = path.read_text().strip()
        if not value or '\n' in value or '\r' in value:
            raise ValueError('Password must be nonempty and one line')
        return value
    password = credential(args.password_file)
    dashboard_password = credential(args.dashboard_password_file)
    if password == dashboard_password:
        raise ValueError('Warehouse and dashboard passwords must differ')
    compose = ['docker', 'compose', '--env-file', str(ROOT / 'database/.env'),
               '-f', str(ROOT / 'database/compose.yaml')]
    subprocess.run(compose + ['config', '--quiet'], check=True)

    def client(command, *, data=b'', output=False, login_password=None):
        # Password goes over stdin, never process arguments or logs. The admin
        # connects through the existing local socket; no credential is rotated.
        shell = 'IFS= read -r PGPASSWORD; export PGPASSWORD; exec "$@"'
        result = subprocess.run(compose + ['exec', '-T', 'postgres', 'sh', '-c', shell, 'sh',
                                          *command],
                                input=(login_password or password).encode() + b'\n' + data,
                                stdout=subprocess.PIPE if output else None,
                                stderr=subprocess.PIPE, check=False)
        if result.returncode:
            # SQL errors can contain a password literal; never echo raw stderr.
            raise RuntimeError('Database operation failed; check connectivity, grants and credentials')
        return result.stdout

    def sql(statement, *, admin=False, database='reaction_warehouse', dashboard=False):
        command = ['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-At', '-U',
                   'postgres' if admin else ('dashboard' if dashboard else 'warehouse'), '-d', database]
        if not admin:
            command += ['-h', '127.0.0.1']
        return client(command, data=statement.encode(), output=True,
                      login_password=dashboard_password if dashboard else password).decode().strip()

    def check():
        for dashboard, identity in [(False, 'warehouse'), (True, 'dashboard')]:
            if sql("SELECT current_user || ':' || current_database();", dashboard=dashboard) != identity + ':reaction_warehouse':
                raise ValueError('Unexpected database/login identity')
            if sql("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
                   "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid) "
                   "FROM pg_roles WHERE rolname = current_user;", dashboard=dashboard) != 'f':
                raise ValueError('Unexpected role privileges')
        if sql("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname IN ('myfastapiapp', 'job_tracker') "
               "AND has_database_privilege(oid, 'reaction_warehouse', 'CONNECT'));", admin=True) != 'f':
            raise ValueError('Another app can connect to the warehouse')
        if sql("SELECT has_schema_privilege('dashboard', 'public', 'CREATE') OR "
               "has_schema_privilege('dashboard', 'analytics', 'CREATE') OR "
               "EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
               "WHERE n.nspname IN ('public', 'analytics') AND c.relkind IN ('r','p','v','m','f') "
               "AND has_table_privilege('dashboard', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'));", admin=True) != 'f':
            raise ValueError('Dashboard has unexpected write privileges')
        sql('BEGIN; CREATE TABLE public.__reaction_probe (id integer); ROLLBACK;')
        if sql('SHOW default_transaction_read_only;', dashboard=True) != 'on':
            raise ValueError('Dashboard read-only default is missing')

    if args.action == 'provision':
        # Add only missing resources. Never reset an existing password/database.
        sql("\\getenv warehouse_password PGPASSWORD\n"
            "SELECT format('CREATE ROLE warehouse LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD %L', :'warehouse_password') "
            "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'warehouse')\\gexec\n"
            "SELECT 'CREATE DATABASE reaction_warehouse OWNER postgres' "
            "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'reaction_warehouse')\\gexec\n",
            admin=True, database='postgres')
        # Check existing identity before adjusting its own database's access.
        if sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='reaction_warehouse';",
                   admin=True) != 'postgres':
            raise ValueError('Unexpected database owner; inspect before proceeding')
        sql("\\getenv dashboard_password PGPASSWORD\n"
            "SELECT format('CREATE ROLE dashboard LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD %L', :'dashboard_password') "
            "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard')\\gexec\n",
            admin=True, dashboard=True, database='postgres')
        # Verify both existing credentials before adding grants. Never rotate passwords.
        for dashboard in (False, True):
            sql('SELECT 1;', dashboard=dashboard)
        sql('REVOKE ALL ON DATABASE reaction_warehouse FROM PUBLIC; '
            'GRANT CONNECT, CREATE ON DATABASE reaction_warehouse TO warehouse; GRANT CONNECT ON DATABASE reaction_warehouse TO dashboard; '
            'REVOKE CREATE ON SCHEMA public FROM PUBLIC; '
            'GRANT USAGE, CREATE ON SCHEMA public TO warehouse; '
            'CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION warehouse; '
            'GRANT USAGE ON SCHEMA public, analytics TO dashboard; '
            'GRANT SELECT ON ALL TABLES IN SCHEMA public, analytics TO dashboard; '
            'ALTER DEFAULT PRIVILEGES FOR ROLE warehouse IN SCHEMA public, analytics '
            'GRANT SELECT ON TABLES TO dashboard; '
            'ALTER ROLE dashboard SET default_transaction_read_only = on;', admin=True)
        check()
        print('Dedicated database/login ready; existing passwords and demo database preserved.')
    elif args.action == 'check':
        check()
        print('Restricted warehouse writer and read-only dashboard login checks passed.')
    elif args.action == 'backup':
        destination = outside_git(args.path)
        if not destination.is_dir():
            raise ValueError('Create a private backup directory first')
        fd, name = tempfile.mkstemp(prefix='reaction-explorer-', suffix='.partial', dir=destination)
        os.close(fd)
        partial = Path(name)
        try:
            archive = client(['pg_dump', '-h', '127.0.0.1', '-U', 'warehouse', '-d',
                              'reaction_warehouse', '-Fc', '--no-owner', '--no-acl'], output=True)
            partial.write_bytes(archive)
            client(['pg_restore', '--file=/dev/null'], data=archive, output=True)
            complete = partial.with_suffix('.dump')
            partial.rename(complete)
            print(f'Backup: {complete}')
        finally:
            partial.unlink(missing_ok=True)
    else:
        archive = outside_git(args.path).read_bytes()
        if args.action == 'verify-backup':
            scratch = 'rx_restore_check_' + uuid.uuid4().hex
            sql(f'CREATE DATABASE {scratch} OWNER postgres;', admin=True, database='postgres')
            try:
                sql(f'REVOKE ALL ON DATABASE {scratch} FROM PUBLIC; '
                    f'GRANT CONNECT, CREATE ON DATABASE {scratch} TO warehouse; '
                    'GRANT USAGE, CREATE ON SCHEMA public TO warehouse;',
                    admin=True, database=scratch)
                client(['pg_restore', '-h', '127.0.0.1', '-U', 'warehouse', '-d', scratch,
                        '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction'], data=archive)
                counts = sql('SELECT count(*) FROM public.experiment;', database=scratch)
                sql('SELECT count(*) FROM analytics.solvent_comparisons;', database=scratch)
                print('Separate verification database restored; experiments=' + counts)
            finally:
                sql(f'DROP DATABASE {scratch};', admin=True, database='postgres')
        else:
            count = sql("SELECT count(*) FROM pg_depend WHERE refclassid='pg_namespace'::regclass "
                        "AND refobjid IN ('public'::regnamespace, 'analytics'::regnamespace) AND classid != 'pg_default_acl'::regclass;")
            if count != '0':
                raise ValueError('Refusing restore: warehouse schemas are not empty; no data was changed')
            restored_sql = client(['pg_restore', '--no-owner', '--no-acl',
                                   '--schema=public', '--schema=analytics', '--file=-'],
                                  data=archive, output=True)
            client(['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1',
                    '-U', 'warehouse', '-d', 'reaction_warehouse', '--single-transaction', '-f', '-'],
                   data=restored_sql)
            print('Restored warehouse. Rerun provision for dashboard grants, then verify data before resuming traffic.')


if __name__ == '__main__':
    main()
