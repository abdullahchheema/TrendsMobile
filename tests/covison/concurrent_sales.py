"""Run after rollback tests on a disposable QA copy; uses two real DB sessions."""
import json,os,subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
SITE='covison-qa.local'
fixtures=Path('sites')/SITE/'private/qa_race.json'
prepare='''import json,frappe
from pathlib import Path
from covison_erp.api import create_sale
assert frappe.conf.covison_qa and frappe.local.site!='frontend'
phone=frappe.get_doc('Mobile Device','COV-PHN-000001')
phone.imei_1='490154203237518';phone.asking_price=65000;phone.minimum_price=50000;phone.save()
customer=frappe.get_doc(dict(doctype='Customer',customer_name='QA simultaneous sale customer',customer_type='Individual',customer_group='Individual',territory='All Territories',default_currency='PKR')).insert()
seller=frappe.db.get_value('Mobile Staff',{'staff_name':'Ali Hassan','enabled':1},'name')
invoices=[create_sale(phone.name,customer.name,65000,sold_by=seller)['name'] for _ in range(2)]
Path(frappe.get_site_path('private','qa_race.json')).write_text(json.dumps({'invoices':invoices,'device':phone.name}))
'''
worker='''import json,os,frappe
from covison_erp.workflows import checkout
assert frappe.conf.covison_qa and frappe.local.site!='frontend'
try:
    checkout(os.environ['COVISON_QA_INVOICE'],payments=[])
    frappe.db.commit()
    print(json.dumps({'invoice':os.environ['COVISON_QA_INVOICE'],'success':True}))
except Exception as error:
    frappe.db.rollback()
    print(json.dumps({'invoice':os.environ['COVISON_QA_INVOICE'],'success':False,'reason':str(error)[:250]}))
'''
Path('/tmp/covison_race_prepare.py').write_text(prepare);Path('/tmp/covison_race_worker.py').write_text(worker)
def execute_file(path,env=None):
    p=subprocess.run(['bench','--site',SITE,'execute',f'exec(open("{path}").read(), {{}})'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
    if p.returncode:raise RuntimeError(p.stdout[-4000:])
    return p.stdout
execute_file('/tmp/covison_race_prepare.py')
fixture=json.loads(fixtures.read_text())
def run(invoice):
    env=dict(os.environ,COVISON_QA_INVOICE=invoice)
    output=execute_file('/tmp/covison_race_worker.py',env)
    return json.loads(next(line for line in output.splitlines() if line.startswith('{')))
with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,fixture['invoices']))
report={'check':'Two simultaneous checkouts for one exact phone','attempts':results,'passed':sum(r['success'] for r in results)==1 and all('Record has changed' not in r.get('reason','') for r in results)}
Path('/tmp/covison-concurrency-results.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
if not report['passed']:raise SystemExit('Concurrent-sale protection failed')
