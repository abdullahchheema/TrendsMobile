"""Add the transaction API's separate credentials without rotating Supabase keys."""
import os,re,secrets
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent
path=ROOT/'server/.env';text=path.read_text()
values=dict(line.split('=',1) for line in text.splitlines() if line and not line.startswith('#') and '=' in line)
host=urlparse(values['SUPABASE_PUBLIC_URL']).hostname
new=dict(COVISON_APP_DB_PASSWORD=secrets.token_hex(24),COVISON_DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
    COVISON_CRM_OWNER_EMAIL='owner@covison.local',COVISON_CRM_OWNER_PASSWORD='Covison'+secrets.token_urlsafe(24),
    COVISON_UI_URL='http://'+host+':8081',DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,crm-web,testserver,'+host)
for key,value in new.items():
    if key not in values:text+='\n'+key+'='+value+'\n';values[key]=value
text=re.sub(r'^SITE_URL=.*$',lambda _: 'SITE_URL='+values['COVISON_UI_URL'],text,flags=re.M)
path.write_text(text);path.chmod(0o600)
login=ROOT/'private/SERVER-LOGIN.txt'
if 'CRM initial login' not in login.read_text():
    with login.open('a') as handle:
        handle.write('\nCRM initial login\nURL '+values['COVISON_UI_URL']+'\nEmail '+values['COVISON_CRM_OWNER_EMAIL']+'\nInitial password '+values['COVISON_CRM_OWNER_PASSWORD']+'\nChange the initial password after signing in.\n')
login.chmod(0o600)
print('Transaction API configuration ready; existing keys and passwords preserved.')
