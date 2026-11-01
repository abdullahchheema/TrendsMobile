"""Read-only proof that screen review did not modify primary business records."""
import json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
baseline=json.loads((ROOT/'artifacts/controlled-demo/isolation-verification.json').read_text())
def sql(query):
    result=subprocess.run(['docker','exec','-i','covison-supabase-db','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres'],input=query,text=True,capture_output=True,check=True)
    return result.stdout.strip()
current={}
for table in baseline['after']:
    assert re.fullmatch(r'shop_[a-z_]+',table)
    current[table]=sql("select count(*)||':'||coalesce(md5(string_agg(row_to_json(t)::text,'' order by row_to_json(t)::text)), 'empty') from covison_app."+table+' t;')
run_id=baseline['run_id'];assert re.fullmatch(r'[0-9a-f]{32}',run_id)
remaining=dict(databases=int(sql("select count(*) from pg_database where datname like 'covison_demo_%' or datname='test_covison_supabase'")),
    auth_users=int(sql("select count(*) from auth.users where email like 'qa-demo-%@example.invalid'")),
    uploaded_objects=int(sql("select count(*) from storage.objects where name like '%"+run_id+"%'")))
result=dict(primary_business_data_unchanged=current==baseline['after'],tables_verified=len(current),remaining_test_resources=remaining,all_test_resources_removed=not any(remaining.values()),before=baseline['after'],after=current)
(ROOT/'artifacts/screen-parity-business-data-verification.json').write_text(json.dumps(result,indent=2))
assert result['primary_business_data_unchanged'],'Primary business records changed during review.'
assert result['all_test_resources_removed'],'A test resource remains.'
print(json.dumps({key:value for key,value in result.items() if key not in ('before','after')},indent=2))
