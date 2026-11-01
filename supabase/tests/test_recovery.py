"""Failure-path checks for backup, restore and scheduling without Docker or secrets."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recovery as r
import operations as ops


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.private = self.root/'private'
        self.private.mkdir()
        self.journal = self.private/'backup-maintenance.json'
        for module, name, value in [(r, 'ROOT', self.root), (r, 'PRIVATE', self.private), (r, 'JOURNAL', self.journal),
                                    (ops, 'PRIVATE', self.private), (ops, 'STATE', self.private/'state.json')]:
            p = patch.object(module, name, value); p.start(); self.addCleanup(p.stop)

    def fixture(self):
        folder = self.root/'backup'
        folder.mkdir()
        for name in r.REQUIRED:
            (folder/name).write_bytes(b'sample')
        r.write_private(folder/'snapshot.json', dict(format=2, fingerprints={'covison_app.shop_device':'1:hash'}))
        r.write_private(folder/'manifest.json', {name:r.digest(folder/name) for name in r.REQUIRED})
        return folder

    def test_checksum_corruption_is_rejected(self):
        folder = self.fixture()
        (folder/'database.dump').write_bytes(b'broken')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            r.manifest_check(folder)

    def test_manifest_cannot_escape_backup_folder(self):
        folder = self.fixture()
        manifest = json.loads((folder/'manifest.json').read_text())
        manifest['../outside'] = '0'*64
        r.write_private(folder/'manifest.json', manifest)
        with self.assertRaisesRegex(RuntimeError, 'manifest'):
            r.manifest_check(folder)

    def test_symlink_member_is_rejected(self):
        if os.name == 'nt': self.skipTest('Requires symlink support')
        folder = self.fixture()
        outside = self.root/'outside'; outside.write_bytes(b'sample')
        (folder/'database.dump').unlink(); (folder/'database.dump').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'member'):
            r.manifest_check(folder)

    def test_archive_path_traversal_is_rejected(self):
        folder = self.root/'archive'; folder.mkdir()
        with tarfile.open(folder/'storage.tar.gz', 'w:gz') as archive:
            info = tarfile.TarInfo('../outside'); info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
            r.storage_inventory(folder)

    def test_private_metadata_permissions(self):
        path = self.private/'status.json'
        r.write_private(path, dict(status='ok'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), dict(status='ok'))

    def test_second_backup_cannot_acquire_lock(self):
        with r.operation_lock():
            with self.assertRaisesRegex(RuntimeError, 'Another backup'):
                with r.operation_lock(): pass
        with r.operation_lock(): pass

    def test_journal_retained_when_restart_fails(self):
        r.write_private(self.journal, dict(previously_running_container_ids=['a'*64]))
        with patch.object(r, 'docker', side_effect=RuntimeError('restart failed')):
            with self.assertRaises(RuntimeError): r.resume_services()
        self.assertTrue(self.journal.exists())

    def test_resume_only_original_services_and_clear_journal_after_health(self):
        ids = ['a'*64, 'b'*64]
        r.write_private(self.journal, dict(previously_running_container_ids=ids))
        with patch.object(r, 'docker') as docker, patch.object(r, 'wait_ready') as ready:
            self.assertTrue(r.resume_services())
        docker.assert_called_once_with('start', *reversed(ids))
        ready.assert_called_once_with(ids)
        self.assertFalse(self.journal.exists())

    def test_snapshot_failure_resumes_services_without_publishing_backup(self):
        states = [[dict(State={'Running':True,'Health':{'Status':'healthy'}})],
                  [dict(Id='a'*64, Config={'Labels':{'com.docker.compose.service':'crm-web'}})]]
        commands = []
        def docker(*args, **kwargs):
            commands.append(args)
            if args[0] == 'ps': return b'container\n'
            return b''
        def resume():
            if self.journal.exists(): self.journal.unlink()
        with patch.object(r, 'docker', side_effect=docker), patch.object(r, 'inspect', side_effect=states), \
             patch.object(r, 'fingerprints', side_effect=RuntimeError('dump failed')), patch.object(r, 'resume_services', side_effect=resume) as resumed, \
             patch.object(r.subprocess, 'run'):
            with self.assertRaisesRegex(RuntimeError, 'dump failed'):
                r.create_backup(self.root/'backups')
        self.assertEqual(resumed.call_count, 2)
        self.assertTrue(any(c[0]=='stop' for c in commands))
        self.assertFalse(any(p.name.startswith('20') for p in (self.root/'backups').iterdir()))
        self.assertFalse(self.journal.exists())

    def test_duplicate_service_containers_are_rejected_before_any_pause(self):
        states = [[dict(State={'Running':True,'Health':{'Status':'healthy'}})],
                  [dict(Id=char*64,Config={'Labels':{'com.docker.compose.service':'crm-web'}}) for char in ('a','b')]]
        with patch.object(r, 'inspect', side_effect=states), patch.object(r, 'docker', return_value=b'a\nb\n') as docker:
            with self.assertRaisesRegex(RuntimeError, 'duplicate Compose'):
                r.create_backup(self.root/'backups')
        self.assertFalse(any(call.args[0]=='stop' for call in docker.call_args_list))
        self.assertFalse(self.journal.exists())

    def test_karachi_schedule_uses_business_time_not_host_time(self):
        settings = dict(timezone='Asia/Karachi', backup_hour=2, backup_minute=0)
        # 21:01 UTC is 02:01 Karachi on the following day.
        current = datetime(2026, 10, 30, 21, 1, tzinfo=timezone.utc)
        self.assertTrue(ops.backup_due(current, '2026-10-30T20:59:00+00:00', settings))
        self.assertFalse(ops.backup_due(current, '2026-10-30T21:00:00+00:00', settings))

    def test_missed_backup_is_due_after_server_returns(self):
        settings = dict(timezone='Asia/Karachi', backup_hour=2, backup_minute=0)
        current = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(ops.backup_due(current, '2026-10-30T22:00:00+00:00', settings))

    def test_no_extra_backup_before_next_due_time(self):
        settings = dict(timezone='Asia/Karachi', backup_hour=2, backup_minute=0)
        current = datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc)
        self.assertFalse(ops.backup_due(current, '2026-10-30T18:00:00+00:00', settings))

    def test_unplugged_mirror_does_not_create_mount_folder(self):
        folder = self.fixture()
        missing = self.root/'missing-drive'
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            ops.mirror_backup(folder, missing)
        self.assertFalse(missing.exists())

    def test_mirror_is_checksum_verified_and_repeatable(self):
        folder = self.fixture()
        mirror = self.root/'external'; mirror.mkdir()
        target = Path(ops.mirror_backup(folder, mirror))
        self.assertEqual(r.manifest_check(folder), r.manifest_check(target))
        self.assertEqual(ops.mirror_backup(folder, mirror), str(target))
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_damaged_existing_mirror_is_rejected(self):
        folder = self.fixture()
        mirror = self.root/'external'; mirror.mkdir()
        target = Path(ops.mirror_backup(folder, mirror))
        (target/'database.dump').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            ops.mirror_backup(folder, mirror)

    def test_launchd_does_not_use_shell_and_paths_support_spaces(self):
        with patch.object(ops.shutil, 'which', return_value='/Applications/Docker App/bin/docker'):
            value = ops.plist()
        self.assertEqual(value['ProgramArguments'][-1], 'scheduled')
        self.assertEqual(value['Umask'], 0o077)
        self.assertEqual(value['StartCalendarInterval'], [{'Minute':0}, {'Minute':30}])

    def test_failed_health_check_retains_recovery_journal(self):
        r.write_private(self.journal, dict(previously_running_container_ids=['a'*64]))
        with patch.object(r, 'docker'), patch.object(r, 'wait_ready', side_effect=RuntimeError('unhealthy')):
            with self.assertRaises(RuntimeError): r.resume_services()
        self.assertTrue(self.journal.exists())

    def test_restore_failure_attempts_database_volume_and_dump_cleanup(self):
        folder = self.fixture()
        commands = []
        def docker(*args, **kwargs):
            commands.append(args)
            if 'pg_restore' in args: raise RuntimeError('restore failed')
            return b''
        with patch.object(r, 'storage_inventory', return_value={}), patch.object(r, 'restore_storage', return_value={}), \
             patch.object(r, 'docker', side_effect=docker):
            with self.assertRaisesRegex(RuntimeError, 'restore failed'):
                r.verify_restore(folder)
        self.assertTrue(any('dropdb' in command for command in commands))
        self.assertTrue(any(command[:2]==('volume','rm') for command in commands))
        self.assertTrue(any('rm' in command and '-f' in command for command in commands))

    def test_corrupt_backup_is_rejected_before_restore_creates_resources(self):
        folder = self.fixture()
        (folder/'database.dump').write_bytes(b'bad')
        with patch.object(r, 'docker') as docker:
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                r.verify_restore(folder)
        docker.assert_not_called()

    def test_failed_backup_is_recorded_for_operator(self):
        with patch.object(ops, 'config', return_value=dict(timezone='Asia/Karachi',backup_hour=2,backup_minute=0,backup_directory=str(self.root/'backups'))), \
             patch.object(ops, 'create_backup', side_effect=RuntimeError('disk unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'disk unavailable'): ops.run()
        state = json.loads(ops.STATE.read_text())
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error'], 'disk unavailable')
        self.assertNotIn('last_backup_at_utc', state)

    def test_pending_mirror_retries_without_creating_another_snapshot(self):
        folder = self.fixture()
        r.write_private(ops.STATE, dict(last_backup=str(folder),last_backup_at_utc=datetime.now(timezone.utc).isoformat(),pending_mirror=True))
        settings = dict(timezone='Asia/Karachi',backup_hour=2,backup_minute=0,mirror_directory=str(self.root/'external'))
        with patch.object(ops, 'config', return_value=settings), patch.object(ops, 'mirror_backup', return_value='copied') as mirror, \
             patch.object(ops, 'create_backup') as backup:
            ops.run(scheduled=True)
        backup.assert_not_called()
        mirror.assert_called_once_with(folder, settings['mirror_directory'])
        self.assertFalse(json.loads(ops.STATE.read_text())['pending_mirror'])


if __name__ == '__main__': unittest.main()
