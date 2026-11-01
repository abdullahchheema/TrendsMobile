"""Create an isolated HTTP demo, verify primary data is unchanged, clean up."""
import atexit,hashlib,json,os,secrets,subprocess,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parent
# Keep the automatic backup from pausing Auth/storage during this isolated demo.
from recovery import operation_lock
backup_guard=operation_lock();backup_guard.__enter__()
atexit.register(backup_guard.__exit__,None,None,None)
env=dict(line.split('=',1) for line in (ROOT/'server/.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
run_id=uuid.uuid4().hex;database='covison_demo_'+run_id;container='covison-demo-'+run_id
out=ROOT/'artifacts/controlled-demo';out.mkdir(exist_ok=True)
private=ROOT/'private'/('demo-'+run_id);private.mkdir(mode=0o700)
created_db=False;created_container=False;accounts={};completed=False;cleanup=[];cleanup_errors=[];final={}

def docker(*args,input=None,check=True):
    result=subprocess.run(['docker',*args],input=input,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if check and result.returncode:raise RuntimeError('Docker '+args[0]+' failed: '+result.stderr[-1000:])
    return result
def sql(db,text):return docker('exec','-i','covison-supabase-db','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',db,input=text).stdout.strip()
def api(path,method='GET',payload=None):
    headers={'apikey':env['SUPABASE_SECRET_KEY'],'Content-Type':'application/json'}
    with urlopen(Request(env['SUPABASE_PUBLIC_URL']+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers,method=method),timeout=20) as response:
        body=response.read();return json.loads(body) if body else {}
tables=['shop_branch','shop_warehouse','shop_staff','shop_party','shop_phonemodel','shop_product','shop_device','shop_imei','shop_document','shop_line','shop_ledgerentry','shop_stockmovement','shop_shopsettings','shop_audit']
def fingerprint():
    return {table:sql('postgres',"select count(*)||':'||coalesce(md5(string_agg(row_to_json(t)::text,'' order by row_to_json(t)::text)), 'empty') from covison_app."+table+' t;') for table in tables}
before=fingerprint()
try:
    docker('exec','covison-supabase-db','createdb','-U','supabase_admin','--owner','covison_app','--template','template0',database);created_db=True
    sql(database,'create schema covison_app authorization covison_app;')
    config=dict(DJANGO_SECRET_KEY=env['COVISON_DJANGO_SECRET_KEY'],DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost',
        DATABASE_HOST='db',DATABASE_NAME=database,DATABASE_USER='covison_app',DATABASE_PASSWORD=env['COVISON_APP_DB_PASSWORD'],DATABASE_SCHEMA='covison_app',
        SUPABASE_INTERNAL_URL='http://api-gw:8000',SUPABASE_PUBLISHABLE_KEY=env['SUPABASE_PUBLISHABLE_KEY'],SUPABASE_SECRET_KEY=env['SUPABASE_SECRET_KEY'])
    path=private/'container.env';path.write_text('\n'.join(k+'='+v for k,v in config.items())+'\n');path.chmod(0o600)
    docker('create','--name',container,'--network','covison-supabase_default','--env-file',str(path),
        '--mount','type=bind,src='+str((ROOT.parent/'offline/imports').resolve())+',dst=/imports,readonly','covison/erp-supabase:development')
    created_container=True;docker('start',container)
    for attempt in range(60):
        ready=docker('exec',container,'python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/',timeout=1)",check=False)
        if ready.returncode==0:break
        time.sleep(1)
    else:raise RuntimeError('Isolated application did not become ready.')
    snapshot=ROOT.parent/'offline/imports/current-crm.json';digest=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    imported=docker('exec','--user','0',container,'python','manage.py','import_frappe','/imports/current-crm.json','--sha256',digest)
    (out/'import.log').write_text(imported.stdout)
    for role in ('owner','sales','cashier','other'):
        email='qa-demo-'+role+'-'+run_id+'@example.invalid';password='DemoOnly'+secrets.token_urlsafe(24)
        account=api('/auth/v1/admin/users','POST',{'email':email,'password':password,'email_confirm':True,'app_metadata':{'controlled_demo':run_id}})
        accounts[role]={'email':email,'password':password,'id':str(uuid.UUID(account['id']))}
    # Numeric, synthetic 14-digit body and a Luhn check digit.
    body='99000999'+str(int(run_id[:8],16)%1000000).zfill(6)
    def valid(number):return sum((int(c)*2//10+int(c)*2%10) if i%2 else int(c) for i,c in enumerate(number))%10==0
    imei=next(body+str(n) for n in range(10) if valid(body+str(n)))
    context={'run_id':run_id,'database':database,'accounts':accounts,'imei':imei}
    context_file=private/'context.json';context_file.write_text(json.dumps(context));context_file.chmod(0o600)
    docker('cp',str(context_file),container+':/tmp/demo-context.json')
    setup="""import json
from pathlib import Path
from django.db import connection
from django.contrib.auth import get_user_model
from shop.models import *
c=json.loads(Path('/tmp/demo-context.json').read_text())
assert connection.settings_dict['NAME']==c['database'] and c['database'].startswith('covison_demo_')
main=Branch.objects.get(code='PK-001')
other=Branch.objects.create(code='QA-OTHER',name='QA other shop',company=main.company)
for role,a in c['accounts'].items():
 u=get_user_model().objects.create_user(username=a['email'],email=a['email'],password=None,is_superuser=role=='owner',is_staff=role=='owner')
 SupabaseIdentity.objects.create(auth_id=a['id'],user=u)
 if role in ('sales','cashier'):
  p=Staff.objects.get(name='Ali Hassan' if role=='sales' else 'Ayesha Malik');p.user=u;p.save()
 elif role=='other':Staff.objects.create(code='QA-OTHER-STAFF',name='QA other cashier',branch=other,user=u,can_handle_payments=True)
print('Isolated QA identities linked; no production staff changes.')
"""
    setup_file=private/'setup.py';setup_file.write_text(setup)
    docker('cp',str(setup_file),container+':/tmp/demo-setup.py')
    docker('exec','--user','0',container,'python','manage.py','shell','-c','exec(open("/tmp/demo-setup.py").read(), {})')
    docker('cp',str(ROOT/'tests/controlled_driver.py'),container+':/tmp/controlled-driver.py')
    outcome=docker('exec','--user','0',container,'python','manage.py','shell','-c','exec(open("/tmp/controlled-driver.py").read(), {})',check=False)
    (out/'http-demo.log').write_text(outcome.stdout+'\n'+outcome.stderr)
    for file in ('controlled-demo-progress.json','controlled-demo-result.json','demo-form-errors.json'):
        docker('cp',container+':/tmp/'+file,str(out/file),check=False)
    if outcome.returncode:raise RuntimeError('The isolated HTTP demo found a failure; see controlled-demo/http-demo.log.')
    completed=True
finally:
    def clean(label,operation):
        try:operation();cleanup.append(label)
        except Exception as error:cleanup_errors.append(label+': '+str(error))
    def delete_files():
        # Delete only this run's synthetic file, whose filename contains its UUID.
        if not sql(database,"select coalesce(to_regclass('covison_app.shop_imei')::text,'');"):return
        paths=sql(database,"select evidence from covison_app.shop_imei where evidence like '%"+run_id+"%';").splitlines()
        if paths:api('/storage/v1/object/pta-evidence','DELETE',{'prefixes':paths})
    if created_db:clean('temporary private attachments removed',delete_files)
    if created_container:
        clean('temporary HTTP server removed',lambda:docker('rm','--force',container))
    if created_db:
        assert database.startswith('covison_demo_') and database.endswith(run_id)
        clean('isolated database removed',lambda:docker('exec','covison-supabase-db','dropdb','-U','supabase_admin','--force',database))
    for role,account in accounts.items():clean('temporary '+role+' account removed',lambda account=account:api('/auth/v1/admin/users/'+account['id'],'DELETE'))
    for file in private.iterdir():file.unlink()
    private.rmdir();cleanup.append('temporary credential files removed')
    after=fingerprint()
    final=dict(run_id=run_id,completed=completed,primary_business_data_unchanged=before==after,before=before,after=after,cleanup=cleanup,cleanup_errors=cleanup_errors)
    (out/'isolation-verification.json').write_text(json.dumps(final,indent=2))
    if before!=after:raise RuntimeError('Primary business data changed during the demo; investigate before proceeding.')
    if cleanup_errors:raise RuntimeError('Demo cleanup needs attention; see isolation-verification.json.')
print(json.dumps({k:v for k,v in final.items() if k not in ('before','after')},indent=2))
