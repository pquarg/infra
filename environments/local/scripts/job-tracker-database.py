#!/usr/bin/env python3
"""Provision and recover only job_tracker on the existing local Compose server."""
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
    parser.add_argument('action', choices=['provision', 'check', 'backup', 'verify-backup', 'restore'])
    parser.add_argument('path', nargs='?', help='Backup directory or archive (outside Git)')
    args = parser.parse_args()
    if (args.action in {'backup', 'verify-backup', 'restore'}) != bool(args.path):
        parser.error('backup needs a directory; verify-backup/restore need an archive')
    password_file = outside_git(args.password_file)
    if not password_file.exists() and args.action == 'provision':
        with open(password_file, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
            file.write(secrets.token_hex(32))
    if password_file.stat().st_mode & 0o077:
        raise ValueError('Password file must be private (chmod 600)')
    password = password_file.read_text().strip()
    if not password or '\n' in password or '\r' in password:
        raise ValueError('Password must be nonempty and one line')
    compose = ['docker', 'compose', '--env-file', str(ROOT / 'database/.env'),
               '-f', str(ROOT / 'database/compose.yaml')]
    subprocess.run(compose + ['config', '--quiet'], check=True)

    def client(command, *, data=b'', output=False):
        # Password goes over stdin, never process arguments or logs. The admin
        # connects through the existing local socket; no credential is rotated.
        shell = 'IFS= read -r PGPASSWORD; export PGPASSWORD; exec "$@"'
        result = subprocess.run(compose + ['exec', '-T', 'postgres', 'sh', '-c', shell, 'sh',
                                          *command],
                                input=password.encode() + b'\n' + data,
                                stdout=subprocess.PIPE if output else None,
                                stderr=subprocess.PIPE, check=False)
        if result.returncode:
            # SQL errors can contain a password literal; never echo raw stderr.
            raise RuntimeError('Database operation failed; check connectivity, grants and credentials')
        return result.stdout

    def sql(statement, *, admin=False, database='job_tracker'):
        command = ['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-At', '-U',
                   'postgres' if admin else 'job_tracker', '-d', database]
        if not admin:
            command += ['-h', '127.0.0.1']
        return client(command, data=statement.encode(), output=True).decode().strip()

    def check():
        if sql("SELECT current_user || ':' || current_database();") != 'job_tracker:job_tracker':
            raise ValueError('Unexpected database/login identity')
        if sql("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
                   "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid) "
                   "FROM pg_roles WHERE rolname = current_user;") != 'f':
            raise ValueError('Unexpected role privileges')
        if sql("SELECT has_database_privilege('myfastapiapp', 'job_tracker', 'CONNECT');",
               admin=True) != 'f':
            raise ValueError('Demo login unexpectedly has tracker database access')
        if sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
               "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') "
               "AND has_table_privilege('job_tracker', c.oid, 'SELECT,INSERT,UPDATE,DELETE');",
               admin=True, database='myfastapiapp') != '0':
            raise ValueError('Tracker login unexpectedly has demo table access')
        sql('BEGIN; CREATE TABLE public.__tracker_probe (id integer); '
            'INSERT INTO public.__tracker_probe VALUES (1); ROLLBACK;')

    if args.action == 'provision':
        # Add only missing resources. Never reset an existing password/database.
        sql("\\getenv tracker_password PGPASSWORD\n"
            "SELECT format('CREATE ROLE job_tracker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD %L', :'tracker_password') "
            "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'job_tracker')\\gexec\n"
            "SELECT 'CREATE DATABASE job_tracker OWNER postgres' "
            "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'job_tracker')\\gexec\n",
            admin=True, database='postgres')
        # Check existing identity before adjusting its own database's access.
        if sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='job_tracker';",
                   admin=True) != 'postgres':
            raise ValueError('Unexpected database owner; inspect before proceeding')
        sql('REVOKE ALL ON DATABASE job_tracker FROM PUBLIC; '
            'GRANT CONNECT ON DATABASE job_tracker TO job_tracker; '
            'REVOKE CREATE ON SCHEMA public FROM PUBLIC; '
            'GRANT USAGE, CREATE ON SCHEMA public TO job_tracker;', admin=True)
        check()
        print('Dedicated database/login ready; existing passwords and demo database preserved.')
    elif args.action == 'check':
        check()
        print('Restricted login, database identity and transactional DDL passed.')
    elif args.action == 'backup':
        destination = outside_git(args.path)
        if not destination.is_dir():
            raise ValueError('Create a private backup directory first')
        fd, name = tempfile.mkstemp(prefix='job-tracker-', suffix='.partial', dir=destination)
        os.close(fd)
        partial = Path(name)
        try:
            archive = client(['pg_dump', '-h', '127.0.0.1', '-U', 'job_tracker', '-d',
                              'job_tracker', '-Fc', '--no-owner', '--no-acl'], output=True)
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
            scratch = 'jt_restore_check_' + uuid.uuid4().hex
            sql(f'CREATE DATABASE {scratch} OWNER postgres;', admin=True, database='postgres')
            try:
                sql(f'REVOKE ALL ON DATABASE {scratch} FROM PUBLIC; '
                    f'GRANT CONNECT ON DATABASE {scratch} TO job_tracker; '
                    'GRANT USAGE, CREATE ON SCHEMA public TO job_tracker;',
                    admin=True, database=scratch)
                client(['pg_restore', '-h', '127.0.0.1', '-U', 'job_tracker', '-d', scratch,
                        '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction'], data=archive)
                if sql('SELECT version_num FROM alembic_version;', database=scratch) != 'e7b13a920ac4':
                    raise ValueError('Restored schema does not match the current release')
                counts = sql("SELECT 'accounts=' || count(*) FROM users;", database=scratch)
                print('Separate verification database restored; schema head verified; ' + counts)
            finally:
                sql(f'DROP DATABASE {scratch};', admin=True, database='postgres')
        else:
            count = sql("SELECT count(*) FROM pg_depend WHERE refclassid='pg_namespace'::regclass "
                        "AND refobjid='public'::regnamespace;")
            if count != '0':
                raise ValueError('Refusing restore: public schema is not empty; no data was changed')
            client(['pg_restore', '-h', '127.0.0.1', '-U', 'job_tracker', '-d', 'job_tracker',
                    '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction'], data=archive)
            print('Restored into empty tracker schema. Verify logins/data before resuming writers.')


if __name__ == '__main__':
    main()
