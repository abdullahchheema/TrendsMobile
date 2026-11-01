"""Import only the verified exported snapshot into an empty application database."""
import hashlib,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
snapshot=root.parent/'offline/imports/current-crm.json'
manifest=json.loads((root/'artifacts/source-manifest.json').read_text())
digest=hashlib.sha256(snapshot.read_bytes()).hexdigest()
if digest!=manifest['sha256']:raise SystemExit('The export and its source manifest do not match.')
raise SystemExit(subprocess.call(['docker','exec','--user','0','covison-supabase-crm','python','manage.py','import_frappe','/imports/current-crm.json','--sha256',digest]))
