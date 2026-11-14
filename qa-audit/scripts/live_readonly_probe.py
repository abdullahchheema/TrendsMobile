"""Read-only probe of a deployed ERP (or website). Safe for production: only GET requests to public pages and
unauthenticated requests to private pages (which must be refused). It never signs in, never sends a key and never writes.

    python live_readonly_probe.py https://your-erp.example  [--samples 12] [--out results.json]

It reports, per address: the status codes seen, the median / p95 / maximum response time, the payload size, and whether the
answer is what a correctly protected system gives (private pages redirect to the login, the order API refuses a request with
no key). Requests are sent one at a time with a pause between them: this measures, it does not load-test."""
import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

# (path, method, what a healthy and protected deployment answers)
ERP_CHECKS=[
    ('/health/','GET',{200}),
    ('/login/','GET',{200}),
    ('/','GET',{302}),
    ('/desk/mobile-shop','GET',{302}),
    ('/desk/point-of-sale','GET',{302}),
    ('/records/Sales%20Invoice/','GET',{302}),
    ('/money/cash-and-bank/','GET',{302}),
    ('/online/orders/','GET',{302}),
    ('/accounting/chart/','GET',{302}),
    ('/audit/','GET',{302}),
    ('/api/online/config/','GET',{401}),
    ('/api/online/orders/TM-DOESNOTEXIST-ABCDEF/','GET',{401}),
    ('/api/online/orders/','GET',{405,401}),
    ('/static/pos.js','GET',{200}),
    ('/product-images/products/x.jpg','GET',{404}),
    ('/admin/','GET',{404}),
]
# Deliberately NOT probed: /.env, /.git/config and similar. Vercel's firewall treats them as an attack and starts challenging the caller's
# address (seen on 2026-11-13: both answered 403 "Vercel Security Checkpoint", which is a refusal, and later requests were challenged).
SECURITY_HEADERS=('strict-transport-security','x-content-type-options','x-frame-options','referrer-policy','content-security-policy')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def fetch(opener,url,method):
    request=urllib.request.Request(url,method=method,headers={'User-Agent':'qa-audit-readonly-probe/1.0','Accept':'text/html,application/json'})
    started=time.perf_counter()
    try:
        with opener.open(request,timeout=30) as response:body=response.read();status=response.status;headers=dict(response.headers)
    except urllib.error.HTTPError as error:
        body=error.read();status=error.code;headers=dict(error.headers)
    return status,(time.perf_counter()-started)*1000,len(body),{k.lower():v for k,v in headers.items()}


def percentile(values,fraction):
    ordered=sorted(values);index=min(len(ordered)-1,max(0,round(fraction*(len(ordered)-1))));return ordered[index]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('base');parser.add_argument('--samples',type=int,default=12);parser.add_argument('--pause',type=float,default=0.4);parser.add_argument('--out')
    args=parser.parse_args();base=args.base.rstrip('/');opener=urllib.request.build_opener(NoRedirect);report=[]
    for path,method,expected in ERP_CHECKS:
        times=[];statuses=set();size=0;headers={}
        for sample in range(args.samples):
            try:status,ms,size,headers=fetch(opener,base+path,method)
            except Exception as error:statuses.add(f'error:{type(error).__name__}');continue
            statuses.add(status)
            if sample:times.append(ms)                 # the first request may include a cold start: reported separately
            else:first=ms
            time.sleep(args.pause)
        row=dict(path=path,method=method,statuses=sorted(map(str,statuses)),expected=sorted(expected),ok=statuses<=expected,first_ms=round(first,1),
                 median_ms=round(statistics.median(times),1) if times else None,p95_ms=round(percentile(times,0.95),1) if times else None,max_ms=round(max(times),1) if times else None,bytes=size,
                 location=headers.get('location',''),cache_control=headers.get('cache-control',''),security_headers={h:headers.get(h,'') for h in SECURITY_HEADERS},set_cookie_flags=headers.get('set-cookie','').split(';',1)[-1].strip() if headers.get('set-cookie') else '')
        report.append(row)
        print(f"{'OK  ' if row['ok'] else 'FAIL'} {method} {path:<48} {','.join(row['statuses']):<8} first {row['first_ms']:>7} ms  median {row['median_ms']} ms  p95 {row['p95_ms']} ms  {size} bytes")
    if args.out:
        with open(args.out,'w',encoding='utf-8') as handle:json.dump(dict(base=base,samples=args.samples,taken=time.strftime('%Y-%m-%d %H:%M:%S %z'),rows=report),handle,indent=1)
    return 0 if all(r['ok'] for r in report) else 1


if __name__=='__main__':sys.exit(main())
