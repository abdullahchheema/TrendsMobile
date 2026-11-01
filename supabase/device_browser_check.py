"""Review the new image on a temporary loopback-only web container; no cutover."""
import argparse,atexit,json,subprocess,socket,time,uuid,tempfile,shutil
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parent
from recovery import operation_lock
backup_guard=operation_lock();backup_guard.__enter__()
atexit.register(backup_guard.__exit__,None,None,None)
parser=argparse.ArgumentParser();parser.add_argument('--clone-database',action='store_true');parser.add_argument('--seed-easy-demo',action='store_true');parser.add_argument('--demo-date');parser.add_argument('--image',default='covison/erp-supabase:development');parser.add_argument('--interactions-only',action='store_true');args=parser.parse_args()
name='covison-device-review-'+uuid.uuid4().hex
with socket.socket() as listener:
    listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
base='http://127.0.0.1:'+str(port)
created=False;proxy_created=False;cleanup=False;code=1;tested_image=None;database_created=False;database_removed=True
database='covison_browser_'+uuid.uuid4().hex;dump='/tmp/'+database+'.dump'
proxy=name+'-proxy';temporary=Path(tempfile.mkdtemp(prefix='covison-device-proxy-'))
try:
    # Keep the review image explicit after main is pinned to a retained release.
    # Credentials stay in this private temporary file, never in logs or argv.
    raw=subprocess.run(['docker','inspect','--format','{{json .Config.Env}}','covison-supabase-crm'],capture_output=True,text=True,check=True)
    environment=[entry for entry in json.loads(raw.stdout) if not entry.startswith('DJANGO_ALLOWED_HOSTS=')]
    environment.append('DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost')
    if args.seed_easy_demo and not args.clone_database:raise RuntimeError('Demo seeding requires an isolated database clone.')
    if args.clone_database:
        subprocess.run(['docker','exec','covison-supabase-db','createdb','-U','supabase_admin','--owner','covison_app','--template','template0',database],capture_output=True,check=True);database_created=True
        subprocess.run(['docker','exec','covison-supabase-db','pg_dump','-U','supabase_admin','-d','postgres','-n','covison_app','-Fc','-f',dump],capture_output=True,check=True)
        subprocess.run(['docker','exec','covison-supabase-db','pg_restore','-U','supabase_admin','--role','covison_app','--no-owner','-d',database,dump],capture_output=True,check=True)
        environment=[value for value in environment if not value.startswith('DATABASE_NAME=')]+['DATABASE_NAME='+database]
    envfile=temporary/'review.env';envfile.touch(mode=0o600);envfile.write_text('\n'.join(environment)+'\n')
    if args.clone_database:
        prefix=['docker','run','--rm','--network','covison-supabase_default','--env-file',str(envfile),'--entrypoint','python',args.image,'manage.py']
        subprocess.run(prefix+['migrate','--noinput'],capture_output=True,check=True)
        if args.seed_easy_demo:
            settings=dict(line.split('=',1) for line in (ROOT/'server/.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
            command=prefix+['seed_easy_demo','--actor',settings['COVISON_CRM_OWNER_EMAIL']]+(['--date',args.demo_date] if args.demo_date else [])
            result=subprocess.run(command,capture_output=True,text=True)
            (ROOT/'artifacts/easy-browser-demo-seed.log').write_text(result.stdout+'\n'+result.stderr)
            if result.returncode:raise RuntimeError('Isolated demo seeding failed; see the local demo seed log.')
    command=['docker','run','--rm','-d','--name',name,'--network','covison-supabase_default','--env-file',str(envfile),'--entrypoint','gunicorn',args.image,'config.wsgi:application','--bind','0.0.0.0:8000','--workers','2','--timeout','60']
    result=subprocess.run(command,text=True,capture_output=True)
    if result.returncode:raise RuntimeError('Temporary review container could not start: '+result.stderr[-500:])
    created=True
    tested_image=subprocess.run(['docker','inspect','--format','{{.Image}}',name],capture_output=True,text=True,check=True).stdout.strip()
    config=temporary/'default.conf'
    config.write_text('server { listen 80; location / { proxy_pass http://'+name+':8000; proxy_set_header Host $http_host; proxy_read_timeout 65s; } }')
    subprocess.run(['docker','create','--name',proxy,'--network','covison-supabase_lan','-p',f'127.0.0.1:{port}:80','--mount',f'type=bind,src={config},dst=/etc/nginx/conf.d/default.conf,readonly','nginx:stable-alpine'],capture_output=True,check=True)
    proxy_created=True
    subprocess.run(['docker','network','connect','covison-supabase_default',proxy],capture_output=True,check=True)
    subprocess.run(['docker','start',proxy],capture_output=True,check=True)
    for _ in range(60):
        try:
            with urlopen(base+'/health/',timeout=2) as response:
                if response.status==200:break
        except Exception:pass
        time.sleep(.3)
    else:raise RuntimeError('Temporary review container did not become healthy.')
    log=ROOT/'artifacts/device-browser.log'
    with log.open('w') as output:
        code=subprocess.run(['node',str(ROOT/'review_browser.mjs'),'after','--device-tests','--base-url='+base]+(['--interactions-only'] if args.interactions_only else [])+(['--isolated-transactions'] if args.clone_database else []),stdout=output,stderr=subprocess.STDOUT).returncode
finally:
    proxy_removed=True
    if proxy_created:
        removed=subprocess.run(['docker','rm','-f',proxy],text=True,capture_output=True)
        proxy_removed=removed.returncode==0 or 'No such container' in removed.stderr
    if created:
        logs=subprocess.run(['docker','logs','--tail','50',name],text=True,capture_output=True)
        (ROOT/'artifacts/device-review-server.log').write_text(logs.stdout+'\n'+logs.stderr)
        internal=subprocess.run(['docker','exec',name,'python','-c',"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/',timeout=3).status)"],text=True,capture_output=True)
        (ROOT/'artifacts/device-review-network.json').write_text(json.dumps({'internal_http_healthy':internal.returncode==0,'internal_status':internal.stdout.strip()},indent=2))
        result=subprocess.run(['docker','rm','-f',name],text=True,capture_output=True)
        cleanup=(result.returncode==0 or 'No such container' in result.stderr) and proxy_removed
    if database_created:
        result=subprocess.run(['docker','exec','covison-supabase-db','dropdb','-U','supabase_admin','--force',database],capture_output=True)
        database_removed=result.returncode==0
        subprocess.run(['docker','exec','covison-supabase-db','rm','-f',dump],capture_output=True)
        cleanup=(cleanup or (not created and not proxy_created)) and database_removed
    shutil.rmtree(temporary)
    folder=ROOT/'artifacts/device-review';folder.mkdir(exist_ok=True)
    (folder/'container-cleanup.json').write_text(json.dumps({'cloned_database':args.clone_database,'cloned_database_removed':database_removed,'primary_database_migrated':False,'tested_image':tested_image,'requested_image':args.image,'temporary_loopback_server':True,'removed':cleanup,'proxy_removed':proxy_removed,'application_kept_on_internal_network':True,'browser_exit_code':code,'primary_server_restarted':False},indent=2))
if not cleanup:raise RuntimeError('Temporary web container cleanup needs attention.')
print(json.dumps({'browser_passed':code==0,'temporary_container_removed':cleanup,'log':'artifacts/device-browser.log'},indent=2))
raise SystemExit(code)
