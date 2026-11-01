"""Initialize one self-hosted server without printing or replacing credentials."""
import argparse,ipaddress,os,re,secrets,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SERVER=ROOT/'server'
parser=argparse.ArgumentParser()
parser.add_argument('--host',default='localhost',help='Stable LAN IP or DNS name of the shared server')
args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9.-]+',args.host):raise SystemExit('Enter a server IP or DNS name without a scheme or port.')
env=SERVER/'.env'
if env.exists():
    print('Server configuration already exists; credentials and URLs were preserved.')
    raise SystemExit(0)
os.umask(0o077)
text=(SERVER/'.env.example').read_text()
def set_value(key,value):
    global text
    value=str(value)
    if re.search(r'^'+re.escape(key)+'=',text,re.M):text=re.sub(r'^'+re.escape(key)+'=.*$',lambda _:key+'='+value,text,flags=re.M)
    else:text+='\n'+key+'='+value+'\n'
url='http://'+args.host+':8765'
try:
    address=ipaddress.ip_address(args.host)
    if address.is_unspecified or not address.is_private:raise SystemExit('Use the shared server’s private LAN IP.')
    bind=args.host
except ValueError:bind='127.0.0.1'
values=dict(COMPOSE_FILE='docker-compose.yml:../compose.covison.yaml',COVISON_API_PORT='8765',COVISON_BIND_ADDRESS=bind,
    SUPABASE_PUBLIC_URL=url,API_EXTERNAL_URL=url+'/auth/v1',SITE_URL=url,ADDITIONAL_REDIRECT_URLS='',
    DASHBOARD_USERNAME='covison',STUDIO_DEFAULT_ORGANIZATION='Covison',STUDIO_DEFAULT_PROJECT='Covison ERP Local',
    OPENAI_API_KEY='',PGRST_DB_SCHEMAS='api,graphql_public',PGRST_DB_EXTRA_SEARCH_PATH='public',
    DISABLE_SIGNUP='true',ENABLE_EMAIL_SIGNUP='true',ENABLE_EMAIL_AUTOCONFIRM='true',ENABLE_PHONE_SIGNUP='false',
    ENABLE_PHONE_AUTOCONFIRM='false',ENABLE_ANONYMOUS_USERS='false',SMTP_ADMIN_EMAIL='owner@covison.local',
    SMTP_HOST='',SMTP_USER='',SMTP_PASS='',SMTP_SENDER_NAME='Covison ERP',POOLER_TENANT_ID='covison-local',
    GLOBAL_S3_BUCKET='covison',STORAGE_TENANT_ID='covison-local',REGION='local')
for key,value in values.items():set_value(key,value)
env.write_text(text);env.chmod(0o600)
# Use the reviewed upstream key generators. Their output includes secrets and is captured, never displayed.
try:
    for script in ('generate-keys.sh','add-new-auth-keys.sh'):
        completed=subprocess.run(['sh','utils/'+script,'--update-env'],cwd=SERVER,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if completed.returncode:raise RuntimeError('The upstream '+script+' did not complete.')
    # The upstream asymmetric generator enables the matching JWT settings in the base compose file.
    # Covison also declares them explicitly in its override.
    text=env.read_text()
    set_value('DASHBOARD_PASSWORD','Covison'+secrets.token_hex(20))
    env.write_text(text);env.chmod(0o600)
    old=SERVER/'.env.old'
    if old.exists():old.unlink()
except Exception:
    # A partial setup must never be mistaken for a usable server configuration.
    env.rename(SERVER/'.env.failed')
    raise
config=dict(line.split('=',1) for line in env.read_text().splitlines() if line and not line.startswith('#') and '=' in line)
private=ROOT/'private';private.mkdir(exist_ok=True);private.chmod(0o700)
(private/'SERVER-LOGIN.txt').write_text('Covison local Supabase server\nDashboard and API '+url+'\nStudio username '+config['DASHBOARD_USERNAME']+'\nStudio password '+config['DASHBOARD_PASSWORD']+'\n\nFull server secrets are in server/.env. Keep that file on the server.\n')
(private/'SERVER-LOGIN.txt').chmod(0o600)
print('Local Supabase configuration created; credentials are in supabase/private/SERVER-LOGIN.txt.')
