"""Exercise local Auth, Data API, RLS, private storage and network isolation.

Only uniquely named disposable QA users, rows and objects are created. No CRM
transactions are changed and no secrets are written to the verification artifact.
"""
import base64,json,secrets,subprocess,time,uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parent
env=dict(line.split('=',1) for line in (ROOT/'server/.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
base=env['SUPABASE_PUBLIC_URL'];checks={};users=[];table='qa_rls_'+uuid.uuid4().hex;object_name='_qa/'+uuid.uuid4().hex+'.txt';object_created=False
public={'apikey':env['SUPABASE_PUBLISHABLE_KEY']};admin={'apikey':env['SUPABASE_SECRET_KEY']}

def http(path,method='GET',data=None,headers=None):
    headers={'Content-Type':'application/json',**(headers or {})}
    raw=data if isinstance(data,bytes) else json.dumps(data).encode() if data is not None else None
    try:
        with urlopen(Request(base+path,data=raw,headers=headers,method=method),timeout=15) as response:return response.status,response.read()
    except HTTPError as error:return error.code,error.read()

def sql(statement):
    result=subprocess.run(['docker','exec','-i','covison-supabase-db','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres'],input=statement,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:raise RuntimeError('Verification SQL failed; no secrets were printed.')
    return result.stdout.strip()

def expect(name,condition):
    if not condition:raise AssertionError('Failed check: '+name)
    checks[name]=True

try:
    status,_=http('/');expect('studio_requires_authentication',status==401)
    basic=base64.b64encode((env['DASHBOARD_USERNAME']+':'+env['DASHBOARD_PASSWORD']).encode()).decode()
    status,_=http('/',headers={'Authorization':'Basic '+basic});expect('studio_owner_login',status==200)
    status,body=http('/rest/v1/rpc/health',headers=public);expect('data_api_available',status==200 and json.loads(body)['backend']=='Supabase')
    status,_=http('/rest/v1/rpc/health');expect('api_key_required',status in (401,403))
    status,_=http('/auth/v1/health',headers=public);expect('local_auth_healthy',status==200)
    status,body=http('/auth/v1/.well-known/jwks.json')
    public_keys=json.loads(body).get('keys',[]) if status==200 else []
    expect('public_jwks_excludes_signing_secrets',bool(public_keys) and all('d' not in key and 'k' not in key and key.get('kty')!='oct' for key in public_keys))
    status,_=http('/auth/v1/signup',method='POST',data={'email':'not-created@example.invalid','password':secrets.token_urlsafe(24)},headers=public)
    expect('public_signup_disabled',status not in (200,201))
    expect('application_role_not_superuser',sql("select not rolsuper and not rolcreatedb and not rolcreaterole from pg_roles where rolname='covison_app'")=='t')
    expect('private_schema_denies_browser_roles',sql("select not has_schema_privilege('anon','covison_app','usage') and not has_schema_privilege('authenticated','covison_app','usage')")=='t')
    tokens=[]
    for index in (1,2):
        email='qa-'+uuid.uuid4().hex+'@example.invalid';password='TestOnly'+secrets.token_urlsafe(24)
        status,body=http('/auth/v1/admin/users',method='POST',data={'email':email,'password':password,'email_confirm':True},headers=admin)
        expect('temporary_auth_user_'+str(index),status in (200,201))
        user=json.loads(body);users.append(str(uuid.UUID(user['id'])))
        status,body=http('/auth/v1/token?grant_type=password',method='POST',data={'email':email,'password':password},headers=public)
        expect('offline_password_sign_in_'+str(index),status==200)
        session=json.loads(body);tokens.append(session)
    status,body=http('/auth/v1/token?grant_type=refresh_token',method='POST',data={'refresh_token':tokens[0]['refresh_token']},headers=public)
    expect('refresh_tokens_work',status==200);tokens[0]=json.loads(body)
    sql(f"create table api.{table}(owner_id uuid not null references auth.users(id) on delete cascade,label text not null); alter table api.{table} enable row level security; revoke all on api.{table} from public,anon,authenticated; grant select on api.{table} to anon,authenticated; grant all on api.{table} to service_role; create policy own_row on api.{table} for select to authenticated using(owner_id=(select auth.uid())); insert into api.{table} values('{users[0]}','A'),('{users[1]}','B'); notify pgrst,'reload schema';")
    status=404
    for _ in range(30):
        status,body=http('/rest/v1/'+table,headers=public)
        if status==200:break
        time.sleep(.2)
    expect('anonymous_cannot_read_private_rows',status==200 and json.loads(body)==[])
    for index,session in enumerate(tokens):
        status,body=http('/rest/v1/'+table,headers={**public,'Authorization':'Bearer '+session['access_token']})
        expect('row_level_scope_'+str(index+1),status==200 and [r['label'] for r in json.loads(body)]==[['A'],['B']][index])
    status,_=http('/rest/v1/'+table+'?owner_id=eq.'+users[0],method='PATCH',data={'label':'unauthorized'},headers={**public,'Authorization':'Bearer '+tokens[0]['access_token']})
    expect('ungranted_writes_rejected',status in (401,403))
    status,_=http('/rest/v1/rpc/health',headers={**public,'Authorization':'Bearer invalid-token'})
    expect('invalid_jwt_rejected',status==401)
    status,_=http('/rest/v1/shop_device',headers={**public,'Accept-Profile':'covison_app'})
    expect('crm_tables_not_exposed_by_data_api',status==406)
    sample=b'Covison disposable storage verification'
    status,body=http('/storage/v1/object/pta-evidence/'+object_name,method='POST',data=sample,headers={**admin,'Content-Type':'text/plain'})
    if status not in (200,201):
        detail=body.decode(errors='replace')
        for value in env.values():
            if len(value)>7:detail=detail.replace(value,'[redacted]')
        print(json.dumps({'stage':'storage_upload','http_status':status,'detail':detail[:400]}))
    expect('private_storage_upload',status in (200,201));object_created=True
    status,body=http('/storage/v1/object/pta-evidence/'+object_name,headers=admin)
    expect('private_storage_owner_download',status==200 and body==sample)
    status,_=http('/storage/v1/object/pta-evidence/'+object_name,headers=public)
    expect('anonymous_storage_download_denied',status not in (200,201))
    status,_=http('/storage/v1/object/pta-evidence/'+object_name,headers={**public,'Authorization':'Bearer '+tokens[0]['access_token']})
    expect('direct_user_file_access_denied_without_crm_permissions',status not in (200,201))
    probe="const net=require('net');const s=net.connect({host:'1.1.1.1',port:443});s.setTimeout(1500);s.on('connect',()=>{s.destroy();process.exit(1)});s.on('timeout',()=>{s.destroy();process.exit(0)});s.on('error',()=>process.exit(0));"
    blocked=subprocess.run(['docker','exec','covison-supabase-studio','node','-e',probe],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0
    expect('backend_operates_without_internet_egress',blocked)
finally:
    if object_created:
        status,_=http('/storage/v1/object/pta-evidence',method='DELETE',data={'prefixes':[object_name]},headers=admin)
        if status not in (200,204):raise RuntimeError('Disposable storage cleanup failed.')
    sql('drop table if exists api.'+table+"; notify pgrst,'reload schema';")
    for uid in users:
        status,_=http('/auth/v1/admin/users/'+uid,method='DELETE',headers=admin)
        if status not in (200,204):raise RuntimeError('Disposable auth-user cleanup failed.')
result={'checks':checks,'count':len(checks),'temporary_test_data_removed':True,'keys_not_printed':True}
(ROOT/'artifacts/server-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
