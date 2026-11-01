"""Compare business data around the additive staff warehouse migration."""
import json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
tables=['shop_branch','shop_warehouse','shop_till','shop_staff','shop_account','shop_party','shop_phonemodel','shop_product','shop_device','shop_imei','shop_document','shop_line','shop_ledgerentry','shop_stockmovement','shop_shopsettings','shop_audit']
def sql(query):
    return subprocess.run(['docker','exec','-i','covison-supabase-db','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres'],input=query,text=True,capture_output=True,check=True).stdout.strip()
current={}
for table in tables:
    row="to_jsonb(t)-'default_warehouse_id'" if table=='shop_staff' else 'to_jsonb(t)'
    current[table]=sql("select count(*)||':'||coalesce(md5(string_agg(("+row+")::text,'' order by ("+row+")::text)),'empty') from covison_app."+table+' t;')
path=ROOT/'artifacts/shop-setup-before.json'
if sys.argv[1:] == ['--capture']:
    path.write_text(json.dumps(current,indent=2));print('Captured pre-upgrade fingerprints for 16 business tables.');raise SystemExit(0)
before=json.loads(path.read_text())
defaults=int(sql('select count(*) from covison_app.shop_staff where default_warehouse_id is not null'))
invalid=int(sql('select count(*) from covison_app.shop_staff s join covison_app.shop_warehouse w on w.id=s.default_warehouse_id where w.branch_id<>s.branch_id or not w.enabled'))
expected=int(sql("select count(*) from covison_app.shop_staff s join covison_app.shop_warehouse w on w.code=s.source->>'warehouse' and w.branch_id=s.branch_id and w.enabled"))
restored=int(sql("select count(*) from covison_app.shop_staff s join covison_app.shop_warehouse w on w.id=s.default_warehouse_id where w.code=s.source->>'warehouse' and w.branch_id=s.branch_id and w.enabled"))
remaining=int(sql("select count(*) from pg_database where datname like 'covison_demo_%' or datname='test_covison_supabase'"))
result=dict(existing_business_data_unchanged=before==current,tables_verified=len(tables),additive_staff_defaults=defaults,invalid_staff_defaults=invalid,expected_source_defaults=expected,restored_source_defaults=restored,remaining_test_databases=remaining,before=before,after=current)
(ROOT/'artifacts/shop-setup-data-verification.json').write_text(json.dumps(result,indent=2))
assert before==current,'Existing business data changed; inspect before proceeding.'
assert remaining==0,'Test database cleanup incomplete.'
assert invalid==0 and expected==restored,'Staff defaults do not match original warehouse assignments.'
print(json.dumps({key:value for key,value in result.items() if key not in ('before','after')},indent=2))
