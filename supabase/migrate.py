"""Apply checked-in SQL once per checksum; never reset or replace a database."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CONTAINER='covison-supabase-db'
def sql(text):
    completed=subprocess.run(['docker','exec','-i',CONTAINER,'psql','-X','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres','-At'],input=text,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if completed.returncode:raise RuntimeError(completed.stderr)
    return completed.stdout.strip()
if '--bootstrap-schema' in sys.argv:
    sql('create schema if not exists api; revoke all on schema api from public; grant usage on schema api to anon,authenticated,service_role;')
    print('Exposed API schema is ready; no business data was changed.')
    raise SystemExit(0)
sql('create schema if not exists covison_internal; revoke all on schema covison_internal from public,anon,authenticated; create table if not exists covison_internal.migrations (name text primary key, checksum text not null, applied_at timestamptz not null default now());')
applied=[]
for path in sorted((ROOT/'migrations').glob('*.sql')):
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    if not re.fullmatch(r'[0-9A-Za-z_-]+\.sql',path.name):raise RuntimeError('Invalid migration filename.')
    # A single PostgreSQL transaction and advisory lock cover both the schema change
    # and its checksum marker. Repeated or simultaneous invocations cannot double-apply.
    operation=r"""begin;
select pg_advisory_xact_lock(817346593);
select not exists(select 1 from covison_internal.migrations where name='%s') as pending \gset
\if :pending
%s
insert into covison_internal.migrations(name,checksum) values ('%s','%s');
\else
do $check$ begin
if exists(select 1 from covison_internal.migrations where name='%s' and checksum<>'%s') then
raise exception 'An applied migration was edited';
end if;
end $check$;
\endif
commit;
""" % (path.name,path.read_text(),path.name,checksum,path.name,checksum)
    result=sql(operation)
    if 'INSERT 0 1' in result:applied.append(path.name)
print(json.dumps({'applied':applied,'existing_migrations_preserved':True}))
