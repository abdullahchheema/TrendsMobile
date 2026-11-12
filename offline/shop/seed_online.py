"""Seed data for the online store: shop selling prices, website specifications and product photos for the demo stock.

* Selling prices: phones with no selling price get cost + 15 %, rounded to PKR 500 (only phones whose price is 0; real prices are never touched).
* Specifications: typical values for the common models in the demo data (required fields filled), only for pages that have none.
* Photos: clean generated illustrations of a phone in the variant's colour (not real photographs). Replace them with real photos in
  Online Store -> Products & Photos whenever you like; nothing else depends on them.
Safe to run more than once: it never overwrites prices, specifications or photos that already exist."""
import hashlib,math,struct,zlib
from decimal import Decimal
from . import online_orders as oo,online_store,product_media,specs as specmod
from .models import Device,Product,ProductImage

COLOURS=[('natural titanium',(176,168,157)),('desert titanium',(190,160,130)),('white titanium',(226,224,219)),('black titanium',(58,58,62)),('blue titanium',(70,86,110)),('titanium',(160,160,160)),
         ('midnight',(32,40,52)),('starlight',(236,228,214)),('graphite',(72,74,78)),('sierra blue',(155,185,214)),('pacific blue',(60,95,120)),('alpine green',(88,110,90)),
         ('shimmer gold',(214,180,120)),('astral black',(36,38,44)),('burgundy',(110,36,52)),('cosmic orange',(224,120,56)),('orange',(236,132,52)),('purple',(170,150,200)),('lavender',(190,175,215)),
         ('pink',(240,190,205)),('yellow',(240,212,100)),('green',(90,140,100)),('teal',(70,150,150)),('blue',(70,110,185)),('red',(200,48,56)),('gold',(220,190,130)),('silver',(205,208,214)),
         ('gray',(130,132,138)),('grey',(130,132,138)),('white',(240,240,240)),('black',(34,34,38)),('cream',(238,228,205)),('beige',(222,205,180))]


def colour_rgb(name):
    n=(name or '').lower()
    for key,rgb in COLOURS:
        if key in n:return rgb
    h=int(hashlib.md5(n.encode()).hexdigest()[:6],16);return (80+h%120,80+(h>>8)%120,80+(h>>16)%120)


def _png(width,height,rows):
    raw=b''.join(b'\x00'+bytes(r) for r in rows)
    def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw,6))+chunk(b'IEND',b'')


def _sdf(px,py,cx,cy,hw,hh,r):
    qx=abs(px-cx)-hw+r;qy=abs(py-cy)-hh+r
    return math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r


def _mix(a,b,t):return tuple(a[i]*(1-t)+b[i]*t for i in range(3))


def phone_png(rgb,size=640):
    """A back-of-phone illustration in the given colour: soft background, shadow, body, camera island."""
    S=size;cx=S/2;cy=S/2;hw=S*0.18;hh=S*0.36;r=S*0.065
    dark=tuple(max(0,c-38) for c in rgb);light=tuple(min(255,c+22) for c in rgb);rows=[]
    for y in range(S):
        row=bytearray()
        for x in range(S):
            t=y/S;bg=_mix((244,246,250),(226,230,238),t)
            shadow=max(0.0,1-math.hypot((x-cx)/(hw*1.25),(y-(cy+hh+S*0.035))/(S*0.03)));col=_mix(bg,(150,155,168),shadow*0.55)
            d=_sdf(x,y,cx,cy,hw,hh,r)
            if d<1.2:
                edge=min(1.0,max(0.0,(1.2-d)/1.2));g=(x-(cx-hw))/(2*hw);body=_mix(light,dark,0.15+0.7*g)
                if d>-4:body=_mix(body,dark,0.5)
                col=_mix(col,body,edge)
                ci=_sdf(x,y,cx-hw+S*0.092,cy-hh+S*0.1,S*0.075,S*0.075,S*0.03)           # camera island
                if ci<1.0 and d<-2:
                    isl=_mix(body,(28,28,32),0.8) if ci<0 else _mix(body,(28,28,32),0.4);col=_mix(col,isl,min(1.0,max(0.0,(1.0-ci))))
                    for ox,oy in ((-0.032,-0.032),(0.032,-0.032),(0.0,0.034)):
                        lens=math.hypot(x-(cx-hw+S*0.092+S*ox),y-(cy-hh+S*0.1+S*oy))
                        if lens<S*0.021:col=_mix(col,(12,12,16),0.95)
                        elif lens<S*0.025:col=_mix(col,(120,124,132),0.7)
                logo=math.hypot(x-cx,y-(cy+hh*0.08))
                if logo<S*0.022 and d<-2:col=_mix(col,light,0.35)
            row+=bytes((int(col[0]),int(col[1]),int(col[2])))
        rows.append(row)
    return _png(S,S,rows)


SPECS={
 'iphone':{'Display':{'Screen size':'6.1 inch','Display type':'Super Retina XDR OLED','Refresh rate':'60 Hz','Screen protection':'Ceramic Shield'},
           'Performance':{'Chipset':'Apple A-series Bionic','RAM':'6 GB','Operating system':'iOS','Storage type':'NVMe'},
           'Camera':{'Main camera':'12 MP wide + 12 MP ultra-wide','Front camera':'12 MP','Video recording':'4K at 60 fps'},
           'Battery':{'Battery':'3200 mAh','Wired charging':'20 W','Wireless charging':'15 W MagSafe'},
           'Connectivity':{'Network':'5G, 4G LTE','SIM':'Nano-SIM + eSIM','Wi-Fi':'Wi-Fi 6','Bluetooth':'5.3','NFC':'Yes','USB':'USB-C'},
           'Body':{'Water / dust resistance':'IP68','Biometrics':'Face ID','Build':'Aluminium / titanium frame, glass back'},'In the box':{'Box contents':'Phone, USB-C cable, documents'}},
 'samsung':{'Display':{'Screen size':'6.2 inch','Display type':'Dynamic AMOLED 2X','Refresh rate':'120 Hz'},'Performance':{'Chipset':'Exynos / Snapdragon flagship','RAM':'8 GB','Operating system':'Android (One UI)'},
            'Camera':{'Main camera':'64 MP + 12 MP ultra-wide + 12 MP telephoto','Front camera':'10 MP','Video recording':'8K at 24 fps'},'Battery':{'Battery':'4000 mAh','Wired charging':'25 W','Wireless charging':'15 W'},
            'Connectivity':{'Network':'5G, 4G LTE','SIM':'Dual Nano-SIM','Wi-Fi':'Wi-Fi 6','NFC':'Yes','USB':'USB-C'},'Body':{'Water / dust resistance':'IP68','Biometrics':'Ultrasonic fingerprint'}},
 'android':{'Display':{'Screen size':'6.7 inch','Display type':'AMOLED','Refresh rate':'120 Hz'},'Performance':{'Chipset':'Octa-core mid-range chipset','RAM':'8 GB','Operating system':'Android'},
            'Camera':{'Main camera':'50 MP main + 8 MP ultra-wide','Front camera':'16 MP','Video recording':'1080p at 60 fps'},'Battery':{'Battery':'5000 mAh','Wired charging':'67 W'},
            'Connectivity':{'Network':'4G LTE / 5G','SIM':'Dual Nano-SIM','Wi-Fi':'Wi-Fi 5/6','NFC':'Yes','USB':'USB-C'},'Body':{'Biometrics':'In-display fingerprint'}},
}
OVERRIDE=[
 ('pro max',{'Display':{'Screen size':'6.7 inch','Refresh rate':'120 Hz'},'Performance':{'RAM':'8 GB'},'Camera':{'Main camera':'48 MP wide + 12 MP ultra-wide + 12 MP telephoto'},'Battery':{'Battery':'4400 mAh'}}),
 ('iphone 17',{'Display':{'Screen size':'6.9 inch','Display type':'LTPO Super Retina XDR OLED','Refresh rate':'120 Hz'},'Performance':{'Chipset':'Apple A19 Pro','RAM':'12 GB','Operating system':'iOS 26'},'Camera':{'Main camera':'48 MP wide + 48 MP ultra-wide + 48 MP telephoto','Front camera':'18 MP'},'Battery':{'Battery':'4800 mAh','Wired charging':'40 W'}}),
 ('iphone 18',{'Display':{'Screen size':'6.9 inch','Display type':'LTPO Super Retina XDR OLED','Refresh rate':'120 Hz'},'Performance':{'Chipset':'Apple A20 Pro','RAM':'12 GB','Operating system':'iOS 27'},'Camera':{'Main camera':'48 MP wide + 48 MP ultra-wide + 48 MP telephoto','Front camera':'18 MP'},'Battery':{'Battery':'4900 mAh','Wired charging':'40 W'}}),
 ('iphone 15',{'Performance':{'Chipset':'Apple A16 / A17 Pro','Operating system':'iOS 17'},'Connectivity':{'USB':'USB-C'}}),
 ('iphone 14',{'Performance':{'Chipset':'Apple A15 / A16 Bionic','Operating system':'iOS 16'},'Connectivity':{'USB':'Lightning'}}),
 ('iphone 13',{'Performance':{'Chipset':'Apple A15 Bionic','Operating system':'iOS 15'},'Connectivity':{'USB':'Lightning'}}),
 ('iphone 12',{'Performance':{'Chipset':'Apple A14 Bionic','Operating system':'iOS 14'},'Connectivity':{'USB':'Lightning'}}),
 ('iphone 11',{'Performance':{'Chipset':'Apple A13 Bionic','RAM':'4 GB','Operating system':'iOS 13'},'Display':{'Display type':'Liquid Retina LCD'},'Battery':{'Battery':'3110 mAh'},'Connectivity':{'Network':'4G LTE','USB':'Lightning'}}),
]


def specs_for(brand,name):
    n=(name or '').lower();b=(brand or '').lower()
    base='iphone' if ('iphone' in n or b=='apple') else 'samsung' if ('samsung' in n or b=='samsung') else 'android'
    out={g:dict(v) for g,v in SPECS[base].items()}
    for key,extra in OVERRIDE:
        if key in n and (base=='iphone'):
            for g,v in extra.items():out.setdefault(g,{}).update(v)
    return out


def round_price(cost):
    raw=cost*Decimal('1.15');return (raw/Decimal(500)).quantize(Decimal('1'),rounding='ROUND_HALF_UP')*Decimal(500)


def run(write=print,dry=False):
    """Returns a dict of counts. Idempotent."""
    done=dict(prices=0,specs=0,photos=0,live=0)
    for d in Device.objects.filter(asking_price__lte=0,status__in=('Available','Holding'),purchase_cost__gt=0).select_related('product'):
        done['prices']+=1
        if not dry:Device.objects.filter(pk=d.pk).update(asking_price=round_price(d.purchase_cost))
    seen=set();cache={}
    for p in Product.objects.filter(enabled=True,serialized=True).select_related('model').order_by('name'):
        if p.pk in seen:continue
        family=list(online_store.family_of(p).select_related('model'));seen.update(f.pk for f in family)
        if not any(Device.objects.filter(product=f,status__in=('Available','Holding')).exists() for f in family):continue
        title=online_store.title_of(p)
        if not any(f.specs for f in family):
            done['specs']+=1
            if not dry:
                found=specs_for(p.model.brand if p.model_id else p.brand,title)
                for f in family:Product.objects.filter(pk=f.pk).update(specs=found)
        if not ProductImage.objects.filter(product__in=[f.pk for f in family]).exists():
            for f in sorted(family,key=lambda x:(x.storage,x.colour)):
                if dry:done['photos']+=1;continue
                key=(f.colour or '').lower()
                if key not in cache:cache[key]=phone_png(colour_rgb(f.colour))
                path,url,size=product_media.save(f.pk,cache[key])
                ProductImage.objects.create(product=f,path=path,url=url,size=size,position=0,alt=f'{title} {f.colour}'.strip()[:200],variant_only=True);done['photos']+=1
            first=sorted(family,key=lambda x:(x.storage,x.colour))[0]
            if not dry:
                path,url,size=product_media.save(first.pk,cache[(first.colour or '').lower()])
                ProductImage.objects.create(product=first,path=path,url=url,size=size,position=0,alt=title[:200],variant_only=False)
        if not dry:
            online_store.refresh_live(p);done['live']+=1 if Product.objects.get(pk=p.pk).online_visible else 0
        write(f'  {title}')
    return done
