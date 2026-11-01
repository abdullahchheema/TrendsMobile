"""Run on a shop PC: public, read-only checks using only Python's standard library."""
import argparse,json,platform,time
from urllib.request import urlopen,Request
from urllib.parse import urlsplit,urljoin
from html.parser import HTMLParser

class Assets(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        for key in ('src','href'):
            value=attrs.get(key,'')
            if value.startswith('/static/'):self.paths.append(value)

def check(base):
    parsed=urlsplit(base)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Use the shop server base URL, such as http://192.168.1.10:8081')
    base=parsed.scheme+'://'+parsed.netloc
    result={'server':base,'client_os':platform.system(),'python':platform.python_version(),'checks':{},'physical_printer_tested':False,'physical_scanner_tested':False,'other_shop_connectivity_tested':False}
    def get(path):
        started=time.monotonic()
        with urlopen(Request(urljoin(base,path),headers={'Cache-Control':'no-cache'}),timeout=8) as response:return response.status,response.read(),round((time.monotonic()-started)*1000)
    try:
        status,body,ms=get('/health/');result['checks']['server_and_database']={'passed':status==200 and json.loads(body).get('status')=='ok','latency_ms':ms}
    except Exception as error:result['checks']['server_and_database']={'passed':False,'error_type':type(error).__name__}
    try:
        status,body,ms=get('/login/');result['checks']['login_page']={'passed':status==200 and b'Covison ERP' in body,'latency_ms':ms}
        parser=Assets();parser.feed(body.decode(errors='replace'))
        for asset in set(parser.paths):
            status,_,ms=get(asset);result['checks']['local_asset:'+asset]={'passed':status==200,'latency_ms':ms}
    except Exception as error:result['checks']['login_and_assets']={'passed':False,'error_type':type(error).__name__}
    result['passed']=all(row['passed'] for row in result['checks'].values())
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',required=True);parser.add_argument('--output')
    args=parser.parse_args();result=check(args.url)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
