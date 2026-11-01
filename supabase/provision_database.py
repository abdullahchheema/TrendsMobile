"""Configure the application role only; no database rows are changed."""
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
values=dict(line.split('=',1) for line in (root/'server/.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
password=values['COVISON_APP_DB_PASSWORD'].replace("'","''")
command="alter role covison_app password '"+password+"'; alter role covison_app set search_path=covison_app,public;"
result=subprocess.run(['docker','exec','-i','covison-supabase-db','psql','-X','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres'],input=command,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
if result.returncode:raise SystemExit('Application database role provisioning failed; no credentials were printed.')
print('Restricted application database role is ready.')
