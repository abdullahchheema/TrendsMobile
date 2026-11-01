"""Create a local PostgreSQL and uploaded-files backup using Docker CLI."""
import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
root=Path(__file__).resolve().parent
folder=root/'backups'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-UTC')
folder.mkdir(parents=True,exist_ok=False)
def run(*args):subprocess.run(['docker','compose','-f','compose.yaml',*args],cwd=root,check=True)
run('exec','-T','db','pg_dump','-U','covison','-d','covison','-Fc','-f','/tmp/covison-backup.dump')
run('cp','db:/tmp/covison-backup.dump',str(folder/'database.dump'))
run('exec','-T','web','tar','-C','/data','-czf','/tmp/covison-files.tar.gz','.')
run('cp','web:/tmp/covison-files.tar.gz',str(folder/'files.tar.gz'))
(folder/'installation.env').write_bytes((root/'.env').read_bytes())
manifest={}
for path in folder.iterdir():
    path.chmod(0o600);manifest[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
(folder/'manifest.json').write_text(json.dumps(manifest,indent=2))
(folder/'manifest.json').chmod(0o600)
print('Backup saved to '+str(folder))
