"""Read-only browser-style HTTP checks using the real Supabase-linked owner."""
import http.cookiejar,json,re
from pathlib import Path
from urllib.parse import quote,urlencode
from urllib.request import Request,build_opener,HTTPCookieProcessor
ROOT=Path(__file__).resolve().parent
env=dict(line.split('=',1) for line in (ROOT/'server/.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
base=env['COVISON_UI_URL'];jar=http.cookiejar.CookieJar();client=build_opener(HTTPCookieProcessor(jar))
login=client.open(base+'/login/').read().decode()
csrf=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',login).group(1)
payload=urlencode(dict(username=env['COVISON_CRM_OWNER_EMAIL'],password=env['COVISON_CRM_OWNER_PASSWORD'],csrfmiddlewaretoken=csrf)).encode()
response=client.open(Request(base+'/login/',data=payload,headers={'Referer':base+'/login/'}))
if not response.url.endswith('/desk/mobile-shop'):raise RuntimeError('The linked owner could not sign in to the CRM.')
html=response.read().decode();assert 'Mobile Shop Dashboard' in html and 'COV-PHN-000001' in html
for name in ('SUPABASE_SECRET_KEY','COVISON_APP_DB_PASSWORD','COVISON_CRM_OWNER_PASSWORD'):
    assert env[name] not in html,'A private value appeared in HTML.'
health=json.loads(client.open(base+'/health/').read());assert health['database']=='Supabase PostgreSQL' and health['authentication']=='Supabase Auth'
paths=['/desk/query-report/Profit%20and%20Loss%20Statement','/desk/query-report/Balance%20Sheet','/desk/query-report/Trial%20Balance','/reports/','/desk/query-report/Stock%20Ledger','/desk/mobile-shop','/desk/point-of-sale','/settings/','/accounts/','/account/password/']
paths += ['/device-check/','/device-check/print/?paper=58','/device-check/print/?paper=80','/device-check/print/?paper=a4']
paths += ['/documents/Stock%20Entry/new/']
paths += ['/fund-transfers/','/fund-transfers/new/']
paths += ['/journals/','/journals/new/']
paths += ['/records/Till/','/master/Till/new/','/records/POS%20Opening%20Entry/','/documents/POS%20Opening%20Entry/new/','/documents/POS%20Closing%20Entry/new/']
paths += ['/setup/tax/','/setup/tax/new/','/setup/','/setup/new/','/records/Shop/','/records/Account/','/master/Account/new/']
sidebar=ROOT.parent/'apps/covison_erp/covison_erp/mobile_shop/sidebar'
for file in sidebar.glob('*/*.json'):
    for entry in json.loads(file.read_text())['items']:
        if entry['type']!='Link':continue
        if entry['link_type']=='Page':paths.append('/desk/'+entry['link_to'])
        elif entry['link_type']=='DocType':paths.append('/records/'+quote(entry['link_to'])+'/')
        elif entry['link_type']=='Report':paths.append('/desk/query-report/'+quote(entry['link_to']))
results={}
for path in paths:
    page=client.open(base+path)
    assert page.status==200,path
    results[path]=page.status
for path in re.findall(r'(?:src|href)="(/static/[^"]+)"',html):
    assert client.open(base+path).status==200;results[path]=200
csrf=next(cookie.value for cookie in jar if cookie.name=='csrftoken')
signed_out=client.open(Request(base+'/logout/',data=urlencode({'csrfmiddlewaretoken':csrf}).encode(),headers={'Referer':base+'/desk/mobile-shop'}))
assert signed_out.url.endswith('/login/')
result=dict(supabase_owner_sign_in=True,database_provider=health['database'],authentication_provider=health['authentication'],
    file_provider=health['storage'],navigation_and_asset_checks=results,private_keys_absent_from_html=True,visual_browser_testing=False)
result['owner_test_session_signed_out']=True
(ROOT/'artifacts/application-http-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
