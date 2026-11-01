"""Local backup schedule, status and interrupted-maintenance recovery."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

from recovery import (BACKUPS, PRIVATE, ROOT, JOURNAL, create_backup, digest,
                      manifest_check, now, operation_lock, resume_services,
                      verify_restore, write_private)

CONFIG = PRIVATE/'operations.json'
STATE = PRIVATE/'operations-state.json'
LABEL = 'com.covison.erp.backup'


def config():
    result = dict(timezone='Asia/Karachi', backup_hour=2, backup_minute=0,
                  backup_directory=str(BACKUPS), mirror_directory=None, verify_interval_days=7)
    if CONFIG.exists():
        result.update(json.loads(CONFIG.read_text()))
    ZoneInfo(result['timezone'])
    if not (0 <= result['backup_hour'] <= 23 and 0 <= result['backup_minute'] <= 59):
        raise ValueError('Invalid scheduled time.')
    if result['verify_interval_days'] < 1:
        raise ValueError('Restore verification interval must be at least one day.')
    return result


def latest_due(current, settings):
    local = current.astimezone(ZoneInfo(settings['timezone']))
    target = local.replace(hour=settings['backup_hour'], minute=settings['backup_minute'], second=0, microsecond=0)
    return target-timedelta(days=1) if local < target else target


def backup_due(current, previous, settings):
    return not previous or datetime.fromisoformat(previous) < latest_due(current, settings)


def mirror_backup(folder, directory):
    # The destination must already exist: an unplugged drive must never silently
    # turn into a new same-disk folder at its old mount path.
    parent = Path(directory).expanduser().resolve()
    folder = Path(folder).resolve()
    if not parent.is_dir():
        raise RuntimeError('The second-copy destination is unavailable.')
    if parent == folder.parent or folder in parent.parents or parent in folder.parents:
        raise RuntimeError('Choose a separate backup-copy directory.')
    incomplete = parent/('.incomplete-'+folder.name)
    target = parent/folder.name
    if target.exists():
        manifest_check(target)
        if digest(target/'manifest.json') != digest(folder/'manifest.json'):
            raise RuntimeError('The second-copy folder already contains different data.')
        return str(target)
    try:
        shutil.copytree(folder, incomplete)
        incomplete.chmod(0o700)
        manifest_check(incomplete)
        incomplete.rename(target)
    except Exception:
        shutil.rmtree(incomplete, ignore_errors=True)
        raise
    return str(target)


def run(scheduled=False):
    settings = config()
    # Do not overlap scheduler/manual executions, including verification and mirror.
    # Separate from the snapshot lock so library operations can acquire that lock.
    state_lock = PRIVATE/'operations-running.lock'
    with operation_lock(state_lock):
        with operation_lock():
            recovered = resume_services()
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        current = datetime.now(timezone.utc)
        if scheduled and not backup_due(current, state.get('last_backup_at_utc'), settings):
            # A failed mirror or verification is retried even after the backup succeeded.
            pending = bool(state.get('pending_verification') or state.get('pending_mirror'))
            if not pending:
                print(json.dumps(dict(status='not_due', interrupted_services_recovered=recovered)))
                return
        state['last_attempt_at_utc'] = now()
        state['status'] = 'running'
        write_private(STATE, state)
        try:
            if not scheduled or backup_due(current, state.get('last_backup_at_utc'), settings):
                folder = create_backup(settings['backup_directory'])
                state.update(last_backup=str(folder), last_backup_at_utc=now())
                state['pending_mirror'] = bool(settings['mirror_directory'])
                last_verify = state.get('last_restore_verified_at_utc')
                state['pending_verification'] = (not last_verify or current-datetime.fromisoformat(last_verify) >= timedelta(days=settings['verify_interval_days']))
                write_private(STATE, state)
            folder = Path(state['last_backup'])
            manifest_check(folder)
            if state.get('pending_verification'):
                verification = verify_restore(folder)
                write_private(PRIVATE/'last-restore-check.json', verification)
                state['last_restore_verified_at_utc'] = now()
                state['pending_verification'] = False
                write_private(STATE, state)
            if state.get('pending_mirror'):
                state['last_mirror'] = mirror_backup(folder, settings['mirror_directory'])
                state['last_mirror_at_utc'] = now()
                state['pending_mirror'] = False
                write_private(STATE, state)
            state.update(status='ok', last_success_at_utc=now(), error=None)
            write_private(STATE, state)
            print(json.dumps(dict(status='ok', backup=state['last_backup'], second_copy_configured=bool(settings['mirror_directory']),
                                  last_restore_verified_at_utc=state.get('last_restore_verified_at_utc'))))
        except Exception as error:
            state.update(status='failed', error=str(error), failed_at_utc=now())
            write_private(STATE, state)
            raise


def plist(settings=None):
    # Absolute program/working/log paths handle spaces and avoid shell expansion.
    docker_path = shutil.which('docker')
    if not docker_path:
        raise RuntimeError('Docker CLI was not found.')
    return dict(Label=LABEL, ProgramArguments=[sys.executable, str(ROOT/'operations.py'), 'scheduled'],
                WorkingDirectory=str(ROOT), RunAtLoad=True,
                StartCalendarInterval=[dict(Minute=0), dict(Minute=30)],
                EnvironmentVariables={'PATH':str(Path(docker_path).parent)+os.pathsep+os.environ.get('PATH', '/usr/bin:/bin')},
                StandardOutPath=str(PRIVATE/'backup-scheduler.log'), StandardErrorPath=str(PRIVATE/'backup-scheduler-error.log'),
                Umask=0o077, ProcessType='Background')


def install():
    if platform.system() != 'Darwin':
        raise RuntimeError('This installer is for macOS. See OPERATIONS.md for Linux and Windows scheduling.')
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    if not CONFIG.exists():
        write_private(CONFIG, config())
    target = Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    target.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to replace a different program with the same label.
    if target.exists():
        old = plistlib.loads(target.read_bytes())
        if old.get('ProgramArguments') != plist()['ProgramArguments']:
            raise RuntimeError('An existing scheduler points to another project; inspect it before replacing.')
    target.write_bytes(plistlib.dumps(plist()))
    target.chmod(0o600)
    domain = 'gui/'+str(os.getuid())
    loaded = subprocess.run(['launchctl', 'print', domain+'/'+LABEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not loaded:
        subprocess.run(['launchctl', 'bootstrap', domain, str(target)], check=True)
    subprocess.run(['launchctl', 'enable', domain+'/'+LABEL], check=True)
    print('Loaded '+str(target)+'. Checks every half hour; due time 02:00 Asia/Karachi; requires this user session and Docker.')


def status():
    settings = config()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    result = dict(settings=settings, state=state, maintenance_recovery_pending=JOURNAL.exists(),
                  backup_due=backup_due(datetime.now(timezone.utc), state.get('last_backup_at_utc'), settings))
    if state.get('last_backup'):
        try:
            manifest_check(state['last_backup'])
            result['latest_backup_checksums_valid'] = True
        except Exception:
            result['latest_backup_checksums_valid'] = False
    if platform.system() == 'Darwin':
        loaded = subprocess.run(['launchctl', 'print', 'gui/'+str(os.getuid())+'/'+LABEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result['scheduler_loaded'] = loaded.returncode == 0
    print(json.dumps(result, indent=2))


def set_mirror(directory):
    parent = Path(directory).expanduser().resolve()
    if not parent.is_dir():
        raise RuntimeError('The second-copy folder must already exist on the mounted destination.')
    with operation_lock(PRIVATE/'operations-running.lock'):
        settings = config()
        if parent == Path(settings['backup_directory']).resolve():
            raise RuntimeError('The second-copy folder must differ from the primary backup directory.')
        settings['mirror_directory'] = str(parent)
        write_private(CONFIG, settings)
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if state.get('last_backup'):
            state['pending_mirror'] = True
            write_private(STATE, state)
    print('Second-copy folder configured. Run the scheduled command to copy and verify the latest backup.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['run', 'scheduled', 'status', 'install', 'recover-services', 'set-mirror'])
    p.add_argument('--directory', type=Path, help='Existing second-copy folder for set-mirror.')
    args = p.parse_args()
    if args.command == 'install': install()
    elif args.command == 'status': status()
    elif args.command == 'recover-services':
        with operation_lock(): print(json.dumps(dict(recovered=resume_services())))
    elif args.command == 'set-mirror':
        if args.directory is None: p.error('set-mirror requires --directory')
        set_mirror(args.directory)
    else: run(args.command == 'scheduled')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Backup operation failed: '+str(error), file=sys.stderr)
        raise SystemExit(1)
