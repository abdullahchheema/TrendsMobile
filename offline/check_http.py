"""Authenticated read-only HTTP and offline-isolation checks on the running app."""
import http.cookiejar,json,re,socket,urllib.parse,urllib.request
from pathlib import Path
from shop.access import NAV,REPORTS

def check():
    base='http://web:8000'
    jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page=client.open(base+'/login/').read().decode()
    csrf=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',page).group(1)
    password=next(line[9:] for line in Path('/data/initial-owner.txt').read_text().splitlines() if line.startswith('Password '))
    payload=urllib.parse.urlencode(dict(username='owner',password=password,csrfmiddlewaretoken=csrf)).encode()
    response=client.open(urllib.request.Request(base+'/login/',data=payload,headers={'Referer':base+'/login/'}));assert response.status==200
    assert response.url.endswith('/desk/mobile-shop'),'Login did not reach the dashboard.'
    html=response.read().decode();assert 'Mobile Shop Dashboard' in html and 'COV-PHN-000001' in html
    paths=['/desk/mobile-shop','/desk/point-of-sale','/settings/','/accounts/']
    paths += ['/records/'+urllib.parse.quote(target)+'/' for _,links in NAV for _,target in links]
    paths += ['/desk/query-report/'+urllib.parse.quote(name) for name in REPORTS]
    results={}
    for path in paths:
        r=client.open(base+path);assert r.status==200,path;results[path]=r.status
    for path in re.findall(r'(?:src|href)="(/static/[^"]+)"',html):
        assert client.open(base+path).status==200;results[path]=200
    blocked=False
    try:
        with socket.create_connection(('1.1.1.1',443),timeout=2):pass
    except OSError:blocked=True
    assert blocked,'The application container unexpectedly has external network access.'
    print(json.dumps(dict(authenticated_http_checks=results,application_internet_egress_blocked=blocked,credentials_not_printed=True),indent=2))

check()
