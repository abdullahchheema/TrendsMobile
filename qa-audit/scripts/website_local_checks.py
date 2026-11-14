"""End-to-end checks of the storefront on a developer machine, against the repository's own mock ERP. No production system is touched.

    node scripts/mock-erp.mjs                      (port 4010, key "mock-key")
    next dev -p 3111   with NEXT_PUBLIC_SUPABASE_URL=http://localhost:4010 NEXT_PUBLIC_SUPABASE_ANON_KEY=x ERP_API_URL=http://localhost:4010 ERP_API_KEY=mock-key
    python website_local_checks.py [--site http://localhost:3111] [--erp http://localhost:4010] [--out results.json]

Each check sends what a browser (or someone tampering with one) would send to the website's own server routes and looks at the answer and
at what reached the ERP. The mock ERP stands in for the real one, so this proves the website's half of the contract; the ERP's half is
proved by offline/shop/tests_audit_online.py with the same payload shape."""
import argparse
import json
import random
import sys
import urllib.error
import urllib.request
import uuid

KEY='mock-key'
results=[]
RUN=random.randint(1,250)          # the site's limiter remembers an address for ten minutes: each run uses its own addresses


def call(base,path,body=None,method=None,ip='10.0.0.1',headers=None):
    ip=ip.replace('10.0.',f'10.{RUN}.')
    data=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request(base+path,data=data,method=method or ('POST' if data is not None else 'GET'),headers={'Content-Type':'application/json','X-Forwarded-For':ip,**(headers or {})})
    try:
        with urllib.request.urlopen(request,timeout=180) as response:raw=response.read();status=response.status
    except urllib.error.HTTPError as error:raw=error.read();status=error.code
    try:parsed=json.loads(raw)
    except ValueError:parsed=None
    return status,parsed,raw.decode('utf-8','replace')


def check(name,ok,detail=''):
    results.append(dict(check=name,status='PASS' if ok else 'FAIL',detail=str(detail)[:300]));print(('PASS ' if ok else 'FAIL ')+name+('' if ok else f'  <- {str(detail)[:300]}'),flush=True)


def order(**changes):
    body=dict(orderId=uuid.uuid4().hex[:16].upper(),items=[dict(key='401||||4500.00',qty=1)],expectedTotal=4750,paymentMethod='Cash on Delivery',name='Test Customer',phone='0300 1234567',line1='House 1, Street 2, Test Town',city='Lahore',province='PUNJAB')
    body.update(changes);return body


def mock_stock(erp,product):
    request=urllib.request.Request(erp+'/rest/v1/web_variants',headers={'apikey':'x','Accept-Profile':'api'})
    with urllib.request.urlopen(request,timeout=30) as response:rows=json.loads(response.read())
    return sum(r['quantity'] for r in rows if str(r['product_id'])==str(product))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--site',default='http://localhost:3111');parser.add_argument('--erp',default='http://localhost:4010');parser.add_argument('--out',default='');args=parser.parse_args();site=args.site;erp=args.erp
    if not any(host in site for host in ('localhost','127.0.0.1')):sys.exit('This script creates test orders. It only runs against a local storefront.')

    call(erp,'/__mock/stock',dict(product_id=401,condition='',pta_status='',device_type='',quantity=60));call(erp,'/__mock/stock',dict(product_id=101,condition='Good',pta_status='PTA',device_type='Used',quantity=3))          # a known starting stock in the mock ERP
    call(site,'/api/revalidate',{},headers={'X-API-Key':KEY})

    # ---- pages
    for path in ('/','/ur','/p/iphone-11','/ur/p/iphone-11','/cart','/checkout','/orders','/help','/search?q=iphone','/deals','/about','/promise','/trade-in','/journal','/legal/privacy','/legal/terms','/robots.txt','/sitemap.xml'):
        status,_,text=call(site,path);check(f'page {path} opens',status==200,status)
        check(f'page {path} carries no server secret',KEY not in text and 'ERP_API_KEY' not in text)
    status,_,text=call(site,'/ur');check('Urdu page is right-to-left',status==200 and 'dir="rtl"' in text)
    status,_,text=call(site,'/p/no-such-phone')
    check('unknown product is kept out of search engines (404, or 200 marked noindex)',status==404 or (status==200 and 'noindex' in text),status)
    results.append(dict(check='unknown product answers with HTTP 404',status='PASS' if status==404 else 'NOTE',detail=f'answered {status}: the page is streamed, so "not found" arrives after the 200 was sent; Next marks it noindex'))
    status,_,_=call(site,'/en');check('/en redirects to the default address',status in (200,308,307),status)

    # ---- cart: priced by the server from the ERP's data
    status,body,_=call(site,'/api/cart/lines',dict(items=[dict(key='401||||4500.00',qty=2)]));totals=(body or {}).get('cart',{}).get('totals',{})
    check('cart: 2 x 4500 + 250 delivery = 9250',status==200 and totals.get('subtotal')==9000 and totals.get('shipping')==250 and totals.get('total')==9250,totals)
    status,body,_=call(site,'/api/cart/lines',dict(items=[dict(key='101|Good|PTA|Used|39500.00',qty=1)]));totals=(body or {}).get('cart',{}).get('totals',{})
    check('cart: delivery is free from 30,000',status==200 and totals.get('shipping')==0 and totals.get('total')==39500,totals)
    status,body,_=call(site,'/api/cart/lines',dict(items=[dict(key='401||||1.00',qty=1)]));cart=(body or {}).get('cart',{})
    check('cart: a variant key with a made-up price is not sold',status==200 and cart.get('hasUnavailable') is True,cart.get('totals'))
    status,body,_=call(site,'/api/cart/lines',dict(items=[dict(key='401||||4500.00',qty=1),dict(key='401||||4500.00',qty=1)]));cart=(body or {}).get('cart',{})
    check('cart: the same variant twice becomes one line of two',status==200 and len(cart.get('lines',[]))==1 and cart.get('totals',{}).get('subtotal')==9000,cart.get('totals'))
    for bad in (dict(items=[dict(key='401||||4500.00',qty=0)]),dict(items=[dict(key='401||||4500.00',qty=-1)]),dict(items=[dict(key='401||||4500.00',qty=1.5)]),dict(items=[dict(key='401||||4500.00',qty=100)]),dict(items='x'),[]):
        status,_,_=call(site,'/api/cart/lines',bad);check(f'cart: invalid body refused {json.dumps(bad)[:40]}',status==400,status)

    # ---- checkout: valid, repeated, tampered
    before=mock_stock(erp,401);first=order();status,body,_=call(site,'/api/checkout',first,ip='10.0.1.1');ref=(body or {}).get('order',{}).get('ref','')
    check('checkout: a valid cash-on-delivery order is accepted',status==201 and ref.startswith('TM-') and body['order']['total']==4750,body)
    check('checkout: the ERP reserved exactly one unit',mock_stock(erp,401)==before-1,(before,mock_stock(erp,401)))
    status,body,_=call(site,'/api/checkout',first,ip='10.0.1.1')
    check('checkout: the same order sent again is the same order, not a second one',status==201 and body['order']['ref']==ref and mock_stock(erp,401)==before-1,body)
    status,body,_=call(site,'/api/checkout',order(expectedTotal=1),ip='10.0.1.2');check('checkout: a total the server does not agree with is refused',status==409 and body['error']['code']=='PRICE_CHANGED',body)
    status,body,_=call(site,'/api/checkout',order(items=[dict(key='401||||1.00',qty=1)],expectedTotal=251),ip='10.0.1.2');check('checkout: a made-up price is refused',status==409 and body['error']['code']=='OUT_OF_STOCK',body)
    status,body,_=call(site,'/api/checkout',order(items=[dict(key='401||||4500.00',qty=99)],expectedTotal=4500*99),ip='10.0.1.2');check('checkout: more than is in stock is refused',status==409,body)
    tampered=order(deliveryFee=0,delivery_fee=0,discount=dict(amount=4000,code='FREE'),payment_status='Paid',paymentStatus='Paid',total=1,price=1);status,body,_=call(site,'/api/checkout',tampered,ip='10.0.1.3')
    check('checkout: extra fields (fee 0, discount, "Paid") are ignored; the customer still owes 4750',status==201 and body['order']['total']==4750,body)
    status,placed,_=call(erp,'/api/online/orders/'+body['order']['ref']+'/',headers={'X-API-Key':KEY}) if status==201 else (0,None,'')
    check('checkout: the order the ERP holds is unpaid and for the full amount',status==200 and placed.get('payment_status')=='Unpaid' and float(placed.get('total'))==4750,placed)
    status,body,_=call(site,'/api/checkout',order(province='SINDH',expectedTotal=4900,city='Karachi'),ip='10.0.1.4');check('checkout: the province fee applies (Sindh 400)',status==201 and body['order']['total']==4900,body)
    status,body,_=call(site,'/api/checkout',order(province='SINDH',expectedTotal=4750,city='Karachi'),ip='10.0.1.4');check('checkout: the default fee for a province with its own fee is refused',status==409,body)
    status,body,_=call(site,'/api/checkout',order(items=[dict(key='101|Good|PTA|Used|39500.00',qty=1)],expectedTotal=39500),ip='10.0.1.5');check('checkout: free delivery at 39,500',status==201 and body['order']['total']==39500,body)
    for name,change in (('invalid phone',dict(phone='12345')),('landline',dict(phone='042 1234567')),('missing address',dict(line1='')),('missing province',dict(province='')),('unknown province',dict(province='MARS')),('unknown payment method',dict(paymentMethod='Bitcoin')),
                        ('short name',dict(name='A')),('honeypot filled',dict(website='http://spam')),('no items',dict(items=[])),('fractional quantity',dict(items=[dict(key='401||||4500.00',qty=1.5)])),('negative quantity',dict(items=[dict(key='401||||4500.00',qty=-1)])),
                        ('negative total',dict(expectedTotal=-1)),('bad order id',dict(orderId='../../x')),('41 lines',dict(items=[dict(key=f'{i}||||1.00',qty=1) for i in range(41)]))):
        status,body,_=call(site,'/api/checkout',order(**change),ip='10.0.1.6');check(f'checkout: {name} is refused before the ERP is asked',status==400,(status,body))
    status,_,_=call(site,'/api/checkout',None,method='POST',ip='10.0.1.6');check('checkout: an empty body is refused',status==400,status)
    status,body,_=call(site,'/api/checkout',order(paymentMethod='Card'),ip='10.0.1.6');check('checkout: a payment method the shop does not offer today is refused',status==409,body)
    call(erp,'/__mock/stock',dict(product_id=401,condition='',pta_status='',device_type='',quantity=60))          # enough stock that only the limiter can say no
    codes=[call(site,'/api/checkout',order(),ip='10.0.9.9')[0] for _ in range(10)];check('checkout: the ninth order in ten minutes from one address is slowed down',codes[:8]==[201]*8 and codes[8]==429,codes)

    # ---- tracking: the order number and the phone it was placed with
    status,body,_=call(site,'/api/orders/track',dict(ref=ref,phone='0300 1234567'),ip='10.0.2.1');shown=sorted((body or {}).get('order',{}))
    check('tracking: the right number and phone show the order',status==200 and body['order']['ref']==ref,body)
    check('tracking: only status fields are shown (no name, address or phone)',status==200 and not ({'name','phone','address','customer','email','city'} & set(shown)),shown)
    status,same,_=call(site,'/api/orders/track',dict(ref=ref,phone='+92 300 1234567'),ip='10.0.2.1');check('tracking: the phone may be written either way',status==200)
    wrong_phone=call(site,'/api/orders/track',dict(ref=ref,phone='0300 7654321'),ip='10.0.2.1');unknown=call(site,'/api/orders/track',dict(ref='TM-AAAAAAAAAAAA-BBBBBB',phone='0300 1234567'),ip='10.0.2.1')
    check('tracking: a wrong phone and an unknown order give the same answer',wrong_phone[0]==404 and unknown[0]==404 and wrong_phone[1]==unknown[1],(wrong_phone[:2],unknown[:2]))
    flipped=ref[:-1]+('A' if ref[-1]!='A' else 'B');status,_,_=call(site,'/api/orders/track',dict(ref=flipped,phone='0300 1234567'),ip='10.0.2.1');check('tracking: a changed check code is refused',status==404,status)
    status,_,_=call(site,'/api/orders/track',dict(ref=ref),ip='10.0.2.1');check('tracking: the phone is required',status==400,status)
    codes=[call(site,'/api/orders/track',dict(ref=f'TM-GUESS{i:07d}-ABCDEF',phone='0300 1234567'),ip='10.0.2.9')[0] for i in range(23)]
    check('tracking: guessing is cut off after 20 tries from one address',codes[:20]==[404]*20 and codes[20:]==[429]*3,codes)

    # ---- the cache-refresh hook and the settings feed
    status,_,_=call(site,'/api/revalidate',{},ip='10.0.3.1');check('revalidate: refused without the key',status==401,status)
    status,_,_=call(site,'/api/revalidate',{},ip='10.0.3.1',headers={'X-API-Key':'wrong'});check('revalidate: refused with a wrong key',status==401,status)
    status,body,_=call(site,'/api/revalidate',{},ip='10.0.3.1',headers={'X-API-Key':KEY});check('revalidate: accepted with the key',status==200,status)
    status,body,text=call(site,'/api/store-config');check('store settings: delivery pricing and payment accounts, nothing secret',status==200 and 'delivery' in (body or {}) and KEY not in text,list((body or {}).keys()))

    # ---- stock taken by an order disappears from the catalogue at once
    left=mock_stock(erp,401);status,body,_=call(site,'/api/cart/lines',dict(items=[dict(key='401||||4500.00',qty=left+1)]));cart=(body or {}).get('cart',{})
    check('cart: asking for more than the stock left is flagged',status==200 and (cart.get('hasUnavailable') or any(l.get('qty',0)<=left for l in cart.get('lines',[]))),cart.get('totals'))

    failed=[r for r in results if r['status']=='FAIL'];notes=[r for r in results if r['status']=='NOTE']
    print(f'\n{len(results)-len(failed)-len(notes)} passed, {len(failed)} failed, {len(notes)} noted, of {len(results)} checks.')
    if args.out:
        with open(args.out,'w',encoding='utf-8') as handle:json.dump(dict(site=site,results=results),handle,indent=1)
    return 1 if failed else 0


if __name__=='__main__':sys.exit(main())
