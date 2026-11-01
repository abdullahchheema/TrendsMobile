"""Read-only HTTP checks against the isolated QA site's session and live assets."""
import http.cookiejar,json,urllib.parse,urllib.request,urllib.error
from pathlib import Path
SITE='covison-qa.local'
credentials=json.loads((Path('sites')/SITE/'private/qa_credentials.json').read_text())
results=[]
def request(path,opener=None,data=None,host=SITE,base='http://backend:8000'):
    headers={'Host':host,'X-Frappe-Site-Name':host}
    body=json.dumps(data).encode() if data is not None else None
    if body is not None:headers['Content-Type']='application/json'
    req=urllib.request.Request(base+path,data=body,headers=headers)
    try:
        response=(opener or urllib.request.build_opener()).open(req,timeout=30)
        return response.status,response.read().decode()
    except urllib.error.HTTPError as e:return e.code,e.read().decode()
def check(name,condition,detail=''):
    results.append({'check':name,'passed':bool(condition),'detail':detail})
status,body=request('/api/method/covison_erp.api.dashboard')
check('Guest cannot read inventory',status in (401,403),str(status))
jar=http.cookiejar.CookieJar();session=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
status,body=request('/api/method/login',session,{'usr':'Administrator','pwd':credentials['password']})
check('QA login',status==200 and json.loads(body).get('message')=='Logged In',str(status))
for name in ('mobile-shop','point-of-sale'):
    status,body=request('/api/method/frappe.desk.desk_page.getpage?name='+name,session)
    check('Page loader: '+name,status==200 and bool(json.loads(body).get('docs')),str(status))
status,body=request('/api/method/covison_erp.api.dashboard?status=All',session)
check('Authenticated inventory API',status==200 and len(json.loads(body).get('message',{}).get('devices',[]))==30,str(status))
status,body=request('/api/resource/Mobile%20Staff?fields='+urllib.parse.quote(json.dumps(['name','staff_name'])),session)
check('Staff directory API',status==200 and len(json.loads(body).get('data',[]))==6,str(status))
status,body=request('/desk/mobile-shop',session)
check('Authenticated desk HTML',status==200 and 'Covison ERP' in body,str(status))
manifest=json.loads(Path('sites/assets/assets.json').read_text())
paths=['/login','/assets/covison_erp/css/covison.css','/assets/covison_erp/js/covison.js',
       '/assets/covison_erp/js/workflows.js','/assets/covison_erp/images/covison-mark.svg',
       '/assets/covison_erp/images/covison-wordmark.svg',manifest['point-of-sale.bundle.js']]
for path in paths:
    status,body=request(path,host='frontend',base='http://frontend:8080')
    check('Served asset: '+path,status==200,str(status))
Path('/tmp/covison-http-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
if not all(r['passed'] for r in results):raise SystemExit('HTTP QA failures')
