"""Restore to disposable resources only, never over the live database."""
import argparse,json
from pathlib import Path
from recovery import create_backup,verify_restore,ROOT,PRIVATE,operation_lock,write_private
if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backup',type=Path,help='Existing backup; otherwise create one first.')
    args=p.parse_args()
    with operation_lock(PRIVATE/'operations-running.lock'):
        result=verify_restore(args.backup or create_backup())
        (ROOT/'artifacts/backup-verification.json').write_text(json.dumps(result,indent=2)+'\n')
        state_path=PRIVATE/'operations-state.json'
        if state_path.exists():
            state=json.loads(state_path.read_text())
            if state.get('last_backup')==result['backup']:
                write_private(PRIVATE/'last-restore-check.json',result)
                state['last_restore_verified_at_utc']=result['checked_at_utc']
                state['pending_verification']=False
                write_private(state_path,state)
    print(json.dumps(result,indent=2))
