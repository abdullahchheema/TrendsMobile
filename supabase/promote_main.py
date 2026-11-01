"""Promote the selected local Supabase CRM, retaining the original for rollback.

This switches this computer's application address. It does not certify client
tax treatment, shop hardware, operator setup or four-premises production use.
"""
import argparse,gzip,hashlib,json,os,re,shutil,subprocess,sys,tarfile,time,uuid
from pathlib import Path
from urllib.request import urlopen
from recovery import ROOT,PRIVATE,create_backup,operation_lock,write_private,now
from main_route import enabled

BACKEND='erp-mobileshop-main-backend-1'
FRONTEND='erp-mobileshop-main-frontend-1'
SOURCE=[FRONTEND,'erp-mobileshop-main-scheduler-1','erp-mobileshop-main-queue-short-1','erp-mobileshop-main-queue-long-1','erp-mobileshop-main-websocket-1',BACKEND]
PROTOTYPE=['covison-offline-web-1','covison-offline-db-1']
STATE=PRIVATE/'main-crm-state.json'


def run(command,log=None):
    result=subprocess.run(command,capture_output=True,text=True,timeout=600)
    if log:
        Path(log).write_text(result.stdout+'\n'+result.stderr);Path(log).chmod(0o600)
    if result.returncode:raise RuntimeError('Command failed: '+command[0]+' '+command[1]+'. Details are in the private migration logs.')
    return result.stdout.strip()


def env_values(text):return dict(line.split('=',1) for line in text.splitlines() if line and not line.startswith('#') and '=' in line)


def update_env(text,changes):
    for key,value in changes.items():
        if re.search(r'^'+re.escape(key)+'=',text,re.M):text=re.sub(r'^'+re.escape(key)+r'=.*$',lambda _:key+'='+value,text,flags=re.M)
        else:text=text.rstrip()+'\n'+key+'='+value+'\n'
    return text


def write_secret(path,text):
    temporary=path.with_name(path.name+'.promotion-'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as handle:handle.write(text)
    temporary.replace(path)


def inspect_containers(names):
    # Never request Config.Env or site credentials in a public diagnostic.
    result={}
    for name in names:
        raw=run(['docker','inspect','--format','{{json .State.Running}}|{{json .HostConfig.RestartPolicy}}',name])
        running,policy=raw.split('|',1);result[name]={'running':json.loads(running),'restart_policy':json.loads(policy)}
    return result


def fingerprint():
    base=['docker','exec','covison-supabase-db','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres','-c']
    tables=run(base+["select tablename from pg_tables where schemaname='covison_app' and tablename like 'shop_%' and tablename not in ('shop_sourcerecord','shop_importrun') order by tablename"]).splitlines()
    if not tables:raise RuntimeError('No application business tables found.')
    queries=[]
    for table in tables:
        if not re.fullmatch(r'shop_[a-z_]+',table):raise RuntimeError('Unexpected application table name.')
        queries.append("select json_build_object('table','"+table+"','value',count(*)||':'||coalesce(md5(string_agg(row_to_json(t)::text,'' order by row_to_json(t)::text)),'empty')) from covison_app."+table+' t')
    rows=run(base+[' union all '.join(queries)]).splitlines()
    return {item['table']:item['value'] for item in map(json.loads,rows)}


def control(*args,log=None):return run([sys.executable,str(ROOT/'control.py'),*args],log)


def health(url):
    with urlopen(url+'/health/',timeout=4) as response:
        data=json.loads(response.read())
        return response.status==200 and data.get('database')=='Supabase PostgreSQL' and data.get('authentication')=='Supabase Auth'


def wait_health(url):
    for _ in range(60):
        try:
            if health(url):return
        except Exception:pass
        time.sleep(.5)
    raise RuntimeError('The promoted address did not pass Supabase application/database health checks.')


def restore_services(state):
    for name,info in state['original_containers'].items():
        policy=info['restart_policy'];rule=policy['Name']
        if rule=='on-failure' and policy.get('MaximumRetryCount'):rule+=':'+str(policy['MaximumRetryCount'])
        run(['docker','update','--restart='+rule,name])
    order=[name for name in state['original_containers'] if name!=FRONTEND]+[FRONTEND]
    for name in order:
        if state['original_containers'][name]['running']:run(['docker','start',name])


def rollback(state):
    if fingerprint()!=state['business_before']:
        raise RuntimeError('Business records have changed since the switch. Keep the selected main CRM active and reconcile new transactions before rollback.')
    directory=Path(state['directory'])
    write_secret(ROOT/'server/.env',(directory/'server.env.before').read_text())
    login=directory/'SERVER-LOGIN.before'
    if login.exists():write_secret(PRIVATE/'SERVER-LOGIN.txt',login.read_text())
    control('up','-d','--wait','--no-deps','--no-build','crm-web','auth','crm-proxy',log=directory/'rollback-supabase.log')
    restore_services(state)
    if state['original_containers'][FRONTEND]['running']:
        restored=False
        for _ in range(60):
            try:
                with urlopen('http://127.0.0.1:8080/api/method/ping',timeout=3) as response:
                    if json.loads(response.read()).get('message')=='pong':restored=True;break
            except Exception:pass
            time.sleep(.5)
        if not restored:raise RuntimeError('Original application has not passed its restored-address check.')
    state.update(phase='rolled_back',rolled_back_at_utc=now());write_private(STATE,state)
    public=ROOT/'artifacts/main-crm-promotion.json'
    if public.exists():
        summary=json.loads(public.read_text());summary.update(phase='rolled_back',rolled_back_at_utc=state['rolled_back_at_utc']);public.write_text(json.dumps(summary,indent=2)+'\n')


def promote():
    if STATE.exists():
        previous=json.loads(STATE.read_text())
        if previous.get('phase')=='complete':print('Supabase + Django is already the selected main CRM.');return
        if previous.get('phase') not in ('rolled_back','failed_before_pause'):raise RuntimeError('A prior promotion needs review; use status before changing it.')
    env_path=ROOT/'server/.env';old_text=env_path.read_text();env=env_values(old_text)
    tag='covison/erp-supabase:main-'+uuid.uuid4().hex[:12]
    changes={'COVISON_MAIN_CRM_ENABLED':'1','COVISON_MAIN_PORT':'8080','COVISON_MAIN_APP_IMAGE':tag,
        'COVISON_UI_URL':'http://'+env['COVISON_BIND_ADDRESS']+':8080','SITE_URL':'http://'+env['COVISON_BIND_ADDRESS']+':8080'}
    new_env={**env,**changes};enabled(new_env)
    token=uuid.uuid4().hex;directory=PRIVATE/('main-promotion-'+token);directory.mkdir(mode=0o700)
    write_secret(directory/'server.env.before',old_text)
    if (PRIVATE/'SERVER-LOGIN.txt').exists():shutil.copyfile(PRIVATE/'SERVER-LOGIN.txt',directory/'SERVER-LOGIN.before');(directory/'SERVER-LOGIN.before').chmod(0o600)
    state={'id':token,'started_at_utc':now(),'directory':str(directory),'phase':'preparing','original_containers':inspect_containers(SOURCE+PROTOTYPE),'business_before':fingerprint(),'main_url':changes['COVISON_UI_URL'],'local_url':'http://localhost:8080','release_image':tag}
    write_private(STATE,state)
    with operation_lock(PRIVATE/'operations-running.lock'):
        print('Backing up the Supabase application and database.',flush=True)
        state['supabase_backup']=str(create_backup());write_private(STATE,state)
        with operation_lock():
            try:
                print('Pausing original CRM entry points and workers.',flush=True)
                run(['docker','stop','--time','30',*SOURCE[:-1]])
                state['phase']='original_paused';write_private(STATE,state)
                remote='/tmp/covison-main-'+token
                run(['docker','exec',BACKEND,'mkdir','-p','-m','700',remote])
                run(['docker','exec','-w','/home/frappe/frappe-bench',BACKEND,'bench','--site','frontend','backup','--with-files','--compress','--ignore-backup-conf','--backup-path',remote+'/backup'],directory/'frappe-backup.log')
                run(['docker','cp',BACKEND+':'+remote+'/backup',str(directory/'frappe-backup')])
                run(['docker','cp',BACKEND+':/home/frappe/frappe-bench/sites/frontend/site_config.json',str(directory/'frappe-backup/site_config.original.json')])
                for path in (directory/'frappe-backup').rglob('*'):
                    if path.is_dir():path.chmod(0o700)
                    elif path.is_file():
                        path.chmod(0o600)
                        if path.name.endswith('.sql.gz'):
                            with gzip.open(path,'rb') as handle:
                                while handle.read(1024*1024):pass
                        elif path.name.endswith(('.tar','.tar.gz','.tgz')):
                            with tarfile.open(path) as archive:archive.getmembers()
                files=[path for path in (directory/'frappe-backup').rglob('*') if path.is_file()]
                if not any(path.name.endswith('.sql.gz') for path in files):raise RuntimeError('Original CRM database backup is missing.')
                state['frappe_backup_files']={str(path.relative_to(directory/'frappe-backup')):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
                print('Exporting a consistent read-only original snapshot, including tax templates.',flush=True)
                run(['docker','cp',str(ROOT.parent/'scripts/export_offline_snapshot.py'),BACKEND+':'+remote+'/export.py'])
                exported=run(['docker','exec','-e','COVISON_SNAPSHOT_PATH='+remote+'/source.json','-w','/home/frappe/frappe-bench',BACKEND,'bench','--site','frontend','execute',"exec(open('"+remote+"/export.py').read(), {})"],directory/'source-export.log')
                manifest=json.loads(exported);source=directory/'fresh-source.json'
                run(['docker','cp',BACKEND+':'+remote+'/source.json',str(source)]);source.chmod(0o600)
                digest=hashlib.sha256(source.read_bytes()).hexdigest()
                if digest!=manifest['sha256']:raise RuntimeError('Fresh original export checksum mismatch.')
                state['source_manifest']=manifest;write_private(STATE,state)
                mount=str(source.resolve())+':/cutover/source.json:ro'
                command=['run','--rm','--no-deps','--user','0','--volume',mount,'--entrypoint','python','crm-web','manage.py','preserve_source_configuration','/cutover/source.json','--sha256',digest]
                control(*command,'--check-only',log=directory/'source-compare.log')
                control('exec','-T','crm-web','python','manage.py','verify_migration',log=directory/'before-reconciliation.log')
                control(*command,log=directory/'source-preserved.log')
                if fingerprint()!=state['business_before']:raise RuntimeError('Unexpected business change before promotion.')
                print('Source rows match. Retaining the original as a stopped rollback installation.',flush=True)
                run(['docker','stop','--time','30',BACKEND,*PROTOTYPE])
                run(['docker','update','--restart=no',*SOURCE,*PROTOTYPE])
                run(['docker','image','tag','covison/erp-supabase:development',tag])
                changes['ADDITIONAL_REDIRECT_URLS']=','.join(dict.fromkeys(value for value in (env.get('ADDITIONAL_REDIRECT_URLS','').split(',')+[env['COVISON_UI_URL'],'http://localhost:8080']) if value))
                write_secret(env_path,update_env(old_text,changes))
                state['phase']='activating';write_private(STATE,state)
                control('up','-d','--wait','--no-deps','--no-build','crm-web','auth','crm-proxy',log=directory/'activate.log')
                for url in ('http://127.0.0.1:8080',changes['COVISON_UI_URL'],env['COVISON_UI_URL']):wait_health(url)
                run([sys.executable,str(ROOT/'verify_app.py')],directory/'application-verification.log')
                control('exec','-T','crm-web','python','manage.py','verify_migration',log=directory/'after-reconciliation.log')
                if fingerprint()!=state['business_before']:raise RuntimeError('Business records changed during the main-address switch.')
                login=PRIVATE/'SERVER-LOGIN.txt'
                if login.exists():write_secret(login,login.read_text().replace(env['COVISON_UI_URL'],changes['COVISON_UI_URL']))
                state.update(phase='complete',completed_at_utc=now(),business_records_unchanged=True,tax_rules_activated=False,client_production_acceptance_complete=False)
                write_private(STATE,state)
                summary={key:state[key] for key in ('id','started_at_utc','completed_at_utc','phase','main_url','local_url','release_image','business_records_unchanged','tax_rules_activated','client_production_acceptance_complete')}
                summary.update(original_crm='Preserved, backed up and stopped; automatic application restart disabled',supabase_backup=state['supabase_backup'],original_backup_directory=str(directory/'frappe-backup'),source_sha256=digest,source_doctype_counts=manifest['counts'],source_control_totals=manifest['expected'],legacy_8081_url=env['COVISON_UI_URL'],legacy_url_uses_same_database=True)
                (ROOT/'artifacts/main-crm-promotion.json').write_text(json.dumps(summary,indent=2)+'\n')
                print('Supabase + Django is the main local CRM at '+state['local_url']+'/desk/mobile-shop',flush=True)
            except Exception:
                try:
                    if fingerprint()==state['business_before']:rollback(state)
                    else:state['phase']='needs_review_main_data_preserved';write_private(STATE,state)
                except Exception:state['phase']='needs_review';write_private(STATE,state)
                raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['apply','status','rollback']);args=parser.parse_args()
    if args.command=='status':
        if not STATE.exists():print('No main-CRM promotion has been recorded.')
        else:
            state=json.loads(STATE.read_text());print(json.dumps({key:state.get(key) for key in ('phase','main_url','local_url','started_at_utc','completed_at_utc','client_production_acceptance_complete')},indent=2))
        return
    with operation_lock(PRIVATE/'main-promotion.lock'):
        if args.command=='apply':promote()
        elif not STATE.exists():print('No main-CRM promotion has been recorded.')
        else:
            with operation_lock(PRIVATE/'operations-running.lock'),operation_lock():rollback(json.loads(STATE.read_text()))


if __name__=='__main__':main()
