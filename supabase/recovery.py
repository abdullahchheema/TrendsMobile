"""Consistent backup and disposable restore rehearsal. Standard library only."""
import argparse
import base64
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT/'private'
BACKUPS = ROOT/'backups'
IMAGE = 'covison/erp-supabase:development'
DB = 'covison-supabase-db'
JOURNAL = PRIVATE/'backup-maintenance.json'
REQUIRED = {'database.dump', 'database-roles.sql', 'database-keys.tar.gz', 'storage.tar.gz', 'server.env', 'snapshot.json'}
SERVICES = ['crm-proxy', 'api-gw', 'crm-web', 'studio', 'meta', 'realtime', 'rest', 'auth', 'storage', 'imgproxy']


def now():
    return datetime.now(timezone.utc).isoformat()


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
    temp.replace(path)


@contextlib.contextmanager
def operation_lock(filename=None):
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    path = Path(filename) if filename else PRIVATE/'backup.lock'
    with path.open('a+b') as handle:
        path.chmod(0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another backup or restore check is running.') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def docker(*args, input=None):
    result = subprocess.run(['docker', *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
    if result.returncode:
        # Docker errors can contain configuration values: do not echo to scheduler logs.
        raise RuntimeError('Docker '+args[0]+' failed (exit '+str(result.returncode)+'). Inspect local service status.')
    return result.stdout


def sql(query, database='postgres'):
    return docker('exec', '-i', DB, 'psql', '-X', '-At', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database, input=query.encode()).decode().strip()


def fingerprints(database='postgres'):
    tables = sql("select schemaname||'.'||tablename from pg_tables where schemaname='covison_app' or (schemaname='auth' and tablename='users') or (schemaname='storage' and tablename in ('objects','buckets')) order by 1", database).splitlines()
    if 'covison_app.shop_device' not in tables:
        raise RuntimeError('Covison schema was not found.')
    result = {}
    for table in tables:
        if not re.fullmatch(r'[a-z_]+\.[a-z_]+', table):
            raise RuntimeError('Unexpected table identifier.')
        result[table] = sql("select count(*)||':'||coalesce(md5(string_agg(h,'' order by h)),'empty') from (select md5(to_jsonb(t)::text) h from "+table+' t) hashes', database)
    return result


def inspect(ids):
    return json.loads(docker('inspect', *ids)) if ids else []


def wait_ready(ids, seconds=150):
    deadline = time.monotonic()+seconds
    while True:
        if all(c['State']['Running'] and c['State'].get('Health', {}).get('Status', 'healthy') == 'healthy' for c in inspect(ids)):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Services did not become healthy; check maintenance journal and service status.')
        time.sleep(2)


def resume_services():
    if not JOURNAL.exists():
        return False
    ids = json.loads(JOURNAL.read_text())['previously_running_container_ids']
    if not all(re.fullmatch(r'[a-f0-9]{64}', item) for item in ids):
        raise RuntimeError('Invalid maintenance journal; inspect it locally.')
    if ids:
        docker('start', *reversed(ids))
        wait_ready(ids)
    JOURNAL.unlink()
    return True


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def manifest_check(folder):
    folder = Path(folder).resolve()
    manifest = json.loads((folder/'manifest.json').read_text())
    if set(manifest) != REQUIRED:
        raise RuntimeError('Backup manifest is missing required files or uses an unsupported version.')
    for name, checksum in manifest.items():
        path = folder/name
        if path.is_symlink() or not path.is_file() or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise RuntimeError('Invalid backup member.')
        if digest(path) != checksum:
            raise RuntimeError('Backup checksum mismatch: '+name)
    snapshot = json.loads((folder/'snapshot.json').read_text())
    if snapshot.get('format') != 2 or not snapshot.get('fingerprints'):
        raise RuntimeError('Unsupported backup metadata.')
    return snapshot


def create_backup(destination=BACKUPS):
    os.umask(0o077)
    with operation_lock():
        resume_services()
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(destination).free < 1024**3:
            raise RuntimeError('Less than 1 GiB free at the backup destination.')
        docker('image', 'inspect', IMAGE)
        state = inspect([DB])[0]['State']
        if not state['Running'] or state.get('Health', {}).get('Status') != 'healthy':
            raise RuntimeError('The database must be healthy before backup.')
        ids = docker('ps', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=covison-supabase').decode().split()
        containers = inspect(ids)
        running = {c['Config']['Labels'].get('com.docker.compose.service'): c['Id'] for c in containers}
        application = next((c for c in containers if c['Config']['Labels'].get('com.docker.compose.service') == 'crm-web'), None)
        if len(running)!=len(containers):
            raise RuntimeError('Stop temporary duplicate Compose service containers before a consistent backup.')
        unexpected = set(running)-set(SERVICES)-{'db'}
        if unexpected:
            raise RuntimeError('Stop QA or optional services before a consistent backup: '+', '.join(sorted(unexpected)))
        stopped = [running[s] for s in SERVICES if s in running]
        token = uuid.uuid4().hex
        folder = destination/('.incomplete-'+token)
        folder.mkdir(mode=0o700)
        helper = 'covison-backup-'+token
        db_temp = '/tmp/covison-backup-'+token
        started = now()
        write_private(JOURNAL, dict(started_at_utc=started, previously_running_container_ids=stopped, incomplete_backup=str(folder)))
        try:
            if stopped:
                docker('stop', '--time', '30', *stopped)
            before = fingerprints()
            docker('exec', DB, 'pg_dump', '-U', 'supabase_admin', '-d', 'postgres', '-Fc', '-f', db_temp+'.dump')
            docker('cp', DB+':'+db_temp+'.dump', str(folder/'database.dump'))
            (folder/'database-roles.sql').write_bytes(docker('exec', DB, 'pg_dumpall', '-U', 'supabase_admin', '--globals-only'))
            docker('exec', '--user', '0', DB, 'tar', '-czf', db_temp+'-keys.tar.gz', '-C', '/etc/postgresql-custom', '.')
            docker('cp', DB+':'+db_temp+'-keys.tar.gz', str(folder/'database-keys.tar.gz'))
            docker('create', '--pull=never', '--name', helper, '--network', 'none', '--user', '0', '--mount', 'type=volume,src=covison-supabase_storage-data,dst=/storage,readonly', IMAGE,
                   'tar', '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner', '-czf', '/tmp/storage.tar.gz', '-C', '/storage', '.')
            docker('start', '--attach', helper)
            if inspect([helper])[0]['State']['ExitCode'] != 0:
                raise RuntimeError('Storage archive creation failed.')
            docker('cp', helper+':/tmp/storage.tar.gz', str(folder/'storage.tar.gz'))
            shutil.copyfile(ROOT/'server/.env', folder/'server.env')
            if before != fingerprints():
                raise RuntimeError('Data changed during maintenance; backup was not published.')
            write_private(folder/'snapshot.json', dict(format=2, started_at_utc=started, captured_at_utc=now(), fingerprints=before,
                consistent_writer_pause=True, application_image=application.get('Config', {}).get('Image', IMAGE) if application else IMAGE,
                application_image_id=application.get('Image') if application else None,
                postgresql_major=17, upstream_release='self-hosted/v0.8.3'))
        finally:
            subprocess.run(['docker', 'rm', '-f', helper], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['docker', 'exec', '--user', '0', DB, 'rm', '-f', db_temp+'.dump', db_temp+'-keys.tar.gz'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            resume_services()
        manifest = {}
        for name in sorted(REQUIRED):
            path = folder/name
            path.chmod(0o600)
            manifest[name] = digest(path)
        write_private(folder/'manifest.json', manifest)
        manifest_check(folder)
        published = destination/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-UTC')+'-'+token[:8])
        folder.rename(published)
        return published


def storage_inventory(folder):
    result = {}
    with tarfile.open(Path(folder)/'storage.tar.gz') as archive:
        for member in archive:
            name = str(PurePosixPath(member.name))
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or member.issym() or member.islnk():
                raise RuntimeError('Unsafe storage archive member.')
            if member.isfile():
                with archive.extractfile(member) as handle:
                    checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
                attrs = {k.removeprefix('SCHILY.xattr.'): base64.b64encode(v.encode('utf-8', 'surrogateescape')).decode() for k, v in member.pax_headers.items() if k.startswith('SCHILY.xattr.')}
                result[name.removeprefix('./')] = dict(sha256=checksum, attrs=attrs)
    return result


def restore_storage(folder, volume):
    expected = storage_inventory(folder)
    docker('run', '--rm', '--pull=never', '--network', 'none', '--user', '0', '--mount', 'type=volume,src='+volume+',dst=/restore',
           '--mount', 'type=bind,src='+str(Path(folder)/'storage.tar.gz')+',dst=/backup/storage.tar.gz,readonly', IMAGE,
           'tar', '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner', '-xzf', '/backup/storage.tar.gz', '-C', '/restore')
    probe = """import base64,hashlib,json,os
from pathlib import Path
result={}
for p in Path('/restore').rglob('*'):
 if p.is_file():
  with p.open('rb') as f: checksum=hashlib.file_digest(f,'sha256').hexdigest()
  result[str(p.relative_to('/restore'))]={'sha256':checksum,'attrs':{a:base64.b64encode(os.getxattr(p,a)).decode() for a in os.listxattr(p)}}
print(json.dumps(result))
"""
    actual = json.loads(docker('run', '--rm', '--pull=never', '--network', 'none', '--user', '0', '--mount', 'type=volume,src='+volume+',dst=/restore,readonly', IMAGE, 'python', '-c', probe))
    if actual != expected:
        raise RuntimeError('Restored storage bytes or extended attributes differ.')
    return actual


def verify_storage_fixture():
    """Exercise nonempty bytes and Linux xattrs even when real storage is empty."""
    token = uuid.uuid4().hex
    volumes = ['covison-verify-fixture-'+token, 'covison-verify-fixture-restore-'+token]
    created = []
    try:
        for volume in volumes:
            docker('volume', 'create', volume); created.append(volume)
        fixture = "from pathlib import Path; import os; p=Path('/fixture/receipt.txt'); p.write_bytes(b'Covison restore fixture\\x00\\xff'); os.setxattr(p,'user.covison-content-type',b'application/octet-stream')"
        docker('run', '--rm', '--pull=never', '--network', 'none', '--user', '0', '--mount', 'type=volume,src='+volumes[0]+',dst=/fixture', IMAGE, 'python', '-c', fixture)
        data = docker('run', '--rm', '--pull=never', '--network', 'none', '--user', '0', '--mount', 'type=volume,src='+volumes[0]+',dst=/fixture,readonly', IMAGE,
                      'tar', '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner', '-czf', '-', '-C', '/fixture', '.')
        with tempfile.TemporaryDirectory(prefix='restore-fixture-', dir=PRIVATE) as directory:
            folder = Path(directory)
            (folder/'storage.tar.gz').write_bytes(data)
            files = restore_storage(folder, volumes[1])
            if len(files) != 1 or not files['receipt.txt']['attrs']:
                raise RuntimeError('Storage fixture did not restore its extended attributes.')
    finally:
        errors = []
        for volume in created:
            try: docker('volume', 'rm', volume)
            except RuntimeError as error: errors.append(str(error))
        if errors: raise RuntimeError('Storage fixture cleanup incomplete.')
    return True


def verify_restore(folder):
    """Restore only to UUID-named disposable resources in the current DB service."""
    os.umask(0o077)
    folder = Path(folder).resolve()
    with operation_lock():
        resume_services()
        snapshot = manifest_check(folder)
        storage_inventory(folder)
        token = uuid.uuid4().hex
        database = 'covison_verify_'+token
        volume = 'covison-verify-storage-'+token
        dump = '/tmp/'+token+'.dump'
        db_created = volume_created = False
        try:
            docker('volume', 'create', volume); volume_created = True
            actual_files = restore_storage(folder, volume)
            docker('exec', DB, 'createdb', '-U', 'supabase_admin', '--template', 'template0', database); db_created = True
            docker('cp', str(folder/'database.dump'), DB+':'+dump)
            docker('exec', '--user', '0', DB, 'pg_restore', '-U', 'supabase_admin', '--exit-on-error', '--dbname', database, dump)
            if fingerprints(database) != snapshot['fingerprints']:
                raise RuntimeError('Restored database does not match the captured snapshot.')
            with tarfile.open(folder/'database-keys.tar.gz') as archive:
                if not any(m.isfile() for m in archive):
                    raise RuntimeError('Database key archive is empty.')
            result = dict(backup=str(folder), checked_at_utc=now(), manifest_checksums_valid=True, database_restored=True,
                fingerprint_tables_verified=len(snapshot['fingerprints']), storage_files_restored=len(actual_files),
                storage_archive_matches=True, storage_files_with_xattrs=sum(bool(v['attrs']) for v in actual_files.values()),
                storage_xattr_fixture_passed=verify_storage_fixture(),
                database_key_archive_readable=True, primary_business_database_restored_over=False)
        finally:
            errors = []
            cleanup = [('exec', '--user', '0', DB, 'rm', '-f', dump)]
            if db_created:
                cleanup.append(('exec', DB, 'dropdb', '-U', 'supabase_admin', '--force', database))
            if volume_created:
                cleanup.append(('volume', 'rm', volume))
            for args in cleanup:
                try:
                    docker(*args)
                except RuntimeError as error:
                    errors.append(str(error))
            if errors:
                raise RuntimeError('Restore rehearsal cleanup incomplete: '+'; '.join(errors))
        result['temporary_resources_removed'] = True
        return result


def backup_main():
    parser = argparse.ArgumentParser(description='Consistent server backup. Briefly pauses Supabase preview services.')
    parser.add_argument('--destination', type=Path, default=BACKUPS)
    args = parser.parse_args()
    print('Complete server backup saved to '+str(create_backup(args.destination)))
