"""A realistic 90-day business story for two shops, posted through the ERP's own functions (never raw database writes).

Additive only: nothing existing is changed or deleted. Every generated customer, supplier, note and document carries the
test-data marker so it can be told apart from real records. Deterministic: the same seed gives the same story."""
import heapq
import itertools
import json
import random
from datetime import timedelta
from decimal import Decimal
from collections import Counter
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Account,Branch,Device,Document,IMEI,Line,LedgerEntry,Party,Product,StockMovement,Warehouse
from . import advanced,bulk,catalog_entry,coa,credit,customer_names,easy_retail,exchange,money,purchase_returns,returns,services as s,shopdefaults,stock_adjust,transfers

D=Decimal
MARK='TEST'                      # parties are named "TEST · …"; documents say "TEST DATA" in their note
DOT='TEST · '

# brand, model, storage, colour, kind, condition, typical buying price (PKR), margin range
PHONES=[
 ('iPhone','11','64GB','Black','Used','Good',52000),('iPhone','12','64GB','Black','Used','Good',78000),('iPhone','12','128GB','White','Used','Very Good',88000),
 ('iPhone','12','128GB','Blue','New','Excellent',135000),('iPhone','13','128GB','Midnight','Used','Good',108000),('iPhone','13 Pro','256GB','Sierra Blue','Used','Very Good',168000),
 ('iPhone','14','128GB','Starlight','Used','Excellent',152000),('iPhone','14 Pro Max','512GB','Deep Purple','Used','Very Good',292000),('iPhone','15 Pro','256GB','Natural Titanium','New','Excellent',385000),
 ('Samsung','S23 Ultra','256GB','Green','Used','Very Good',218000),('Samsung','A15','128GB','Black','New','Excellent',38500),('Samsung','A54','128GB','Awesome Violet','Used','Good',62000),
 ('Samsung','S22','128GB','Phantom Black','Used','Fair',96000),('Samsung','A25','128GB','Blue','New','Excellent',58000),('Samsung','Z Flip 4','256GB','Bora Purple','Used','Good',128000),
 ('Google Pixel','8 Pro','256GB','Obsidian','Used','Very Good',172000),('Google Pixel','7','128GB','Snow','Used','Good',92000),('Google Pixel','6a','128GB','Charcoal','Used','Fair',58000),
 ('Xiaomi','13T','256GB','Meadow Green','New','Excellent',96000),('Xiaomi','12','256GB','Blue','Used','Good',78000),('Redmi','Note 12','128GB','Ice Blue','New','Excellent',39500),
 ('Redmi','12','128GB','Sky Blue','New','Excellent',33500),('Redmi','Note 11','128GB','Graphite Gray','Used','Good',27000),('OnePlus','11','256GB','Titan Black','Used','Very Good',142000),
 ('OnePlus','Nord CE 3','128GB','Aqua Surge','New','Excellent',52000),('Oppo','Reno 8','256GB','Shimmer Gold','Used','Good',72000),('Oppo','A78','128GB','Mist Black','New','Excellent',46000),
 ('Vivo','V27','256GB','Magic Blue','New','Excellent',84000),('Vivo','Y36','128GB','Vibrant Gold','New','Excellent',41500),('Vivo','Y27','128GB','Burgundy Black','Used','Good',29000),
 ('Infinix','Note 30','256GB','Magic Black','New','Excellent',36500),('Infinix','Hot 30','128GB','Racing Black','New','Excellent',27500),('Infinix','Zero 30','256GB','Golden Hour','Used','Good',48000),
 ('Tecno','Camon 20','256GB','Serenity Blue','New','Excellent',39500),('Tecno','Spark 10','128GB','Meta Black','New','Excellent',24500),('Tecno','Phantom X2','256GB','Stardust Grey','Used','Good',74000),
 ('Realme','11 Pro','256GB','Astral Black','New','Excellent',86000),('Realme','C55','128GB','Rainforest','New','Excellent',28500),('Realme','GT Neo 3','256GB','Nitro Blue','Used','Good',64000),
 ('Motorola','Edge 30','256GB','Meteor Grey','Used','Good',60000),('Motorola','G54','128GB','Pearl Blue','New','Excellent',36500),('Huawei','Nova 9','128GB','Starry Blue','Used','Good',55000),
 ('Huawei','Y9a','128GB','Midnight Black','Used','Fair',24000),('iPhone','12 Pro','256GB','Pacific Blue','Refurbished','Very Good',118000),('Samsung','S21 FE','128GB','Graphite','Refurbished','Good',72000),
]
ACCESSORIES=[('Fast charger 20W',1600,2400),('USB-C cable',350,700),('Lightning cable',450,900),('Back cover (assorted)',250,650),('Tempered glass',120,400),('Wired earphones',500,1100),
             ('Bluetooth earbuds',2400,4200),('Power bank 10000mAh',3300,5200),('Phone stand',300,650),('Memory card 64GB',1100,1800)]
SUPPLIERS=['Hafeez Centre Traders','Capital Mobile Mart','Al-Noor Imports','Rizwan Wholesale','Karachi Gadget Hub','Lahore Mobile Wholesale','Pindi Used Phones Dealer','Gulberg Accessories House']
FIRST=['Ahmed','Ali','Usman','Hamza','Bilal','Imran','Kamran','Faisal','Zeeshan','Salman','Asad','Naveed','Shahid','Tariq','Waqas','Adeel','Rashid','Junaid','Fahad','Saad','Ayesha','Fatima','Sana','Hina','Maryam','Zainab','Nadia','Sadia','Iqra','Rabia']
LAST=['Khan','Malik','Butt','Raza','Qureshi','Sheikh','Chaudhry','Siddiqui','Abbasi','Awan','Mirza','Hussain','Javed','Iqbal','Rehman','Farooq','Anwar','Shah','Baig','Niazi']
METHODS=['Cash','Bank Transfer','JazzCash','Easypaisa','Card']
EXPENSES=[('COA-6100','Shop rent',(90000,180000),'monthly',1),('COA-6300','Electricity bill',(32000,70000),'monthly',6),('COA-6400','Internet and phone bill',(5500,8000),'monthly',8),
          ('COA-6200','Staff salary',(32000,55000),'monthly',28),('COA-6120','Tea and shop supplies',(1200,3500),'random',0),('COA-6410','Mobile load',(500,2000),'random',0),
          ('COA-6500','Transport / courier',(600,3000),'random',0),('COA-6720','Repair parts and labour',(1500,9000),'random',0),('COA-6550','Packaging and stationery',(800,3000),'random',0),
          ('COA-6450','Security and guard',(12000,16000),'monthly',3),('COA-6750','Commission paid',(1000,6000),'random',0)]


def luhn_imei(rng,used):
    while True:
        body='35'+''.join(rng.choice('0123456789') for _ in range(12))
        for c in '0123456789':
            n=body+c
            if s.valid_imei(n) and n not in used and not IMEI.objects.filter(number=n).exists():used.add(n);return n


def round_to(value,step=500):return D(int(round(float(value)/step))*step)


class Story:
    def __init__(self,owner,shops,tag='TEST-2026-11',seed=20261110,days=90,today=None,log=print):
        self.owner=owner;self.shops=list(shops);self.tag=tag;self.rng=random.Random(seed);self.days=days;self.today=today or timezone.localdate();self.log=log
        self.used=set();self.counts=Counter();self.failures=[];self.customers={};self.suppliers={};self.queue=[];self.tiebreak=itertools.count();self.accessory_products=[]
        self.sold_log=[];self.bill_log=[];self.note=f'TEST DATA {tag}'

    # ------------------------------------------------------------------ plumbing
    def attempt(self,label,fn,*args,**kw):
        """Run one business event. A rejection is counted and remembered; it never aborts the story."""
        try:
            with transaction.atomic():result=fn(*args,**kw)
            self.counts['ok '+label]+=1;return result
        except (ValidationError,PermissionDenied) as error:
            self.counts['rejected '+label]+=1;self.failures.append((label,' '.join(getattr(error,'messages',[str(error)]))[:200]));return None
        except Exception as error:         # a genuine crash: remember it clearly, keep going
            self.counts['CRASH '+label]+=1;self.failures.append((label,'CRASH '+repr(error)[:300]));return None

    def person(self,branch):return shopdefaults.person(self.owner,branch)
    def wh(self,branch):return shopdefaults.warehouse(branch)
    def account(self,code):return Account.objects.get(code=code)

    def cash(self,branch,method='Cash'):
        return next((b['amount'] for b in money.balances([branch]) if b['method']==method),D(0))

    def ensure_funds(self,branch,method,amount,day):
        if method!='Cash' and self.cash(branch,method)<amount:                      # wallets and bank are topped up from cash, like a real deposit
            self.ensure_funds(branch,'Cash',amount,day);self.attempt('deposit',money.move_money,self.owner,branch,'Cash',method,round_to(amount+D(20000),1000),day,'','Top-up '+self.note)
            return
        if self.cash(branch,method)<amount:
            top=round_to(amount*D('1.15')+D(250000),50000);money.record_voucher(self.owner,branch,'capital',self.account('COA-3100'),top,'Cash',day,'Owner funds '+self.note);self.counts['owner funds']+=1

    # ------------------------------------------------------------------ people
    def customer(self,branch,day):
        pool=self.customers.setdefault(branch.pk,[])
        if pool and self.rng.random()<0.62:return self.rng.choice(pool)
        name=f"{DOT}{self.rng.choice(FIRST)} {self.rng.choice(LAST)}";phone='03'+self.rng.choice(['00','01','02','03','12','21','33','45','46'])+''.join(self.rng.choice('0123456789') for _ in range(7))
        c=dict(name=name,phone=phone)
        if not any(x['name']==name for x in pool):pool.append(c)
        else:c=next(x for x in pool if x['name']==name)
        return c

    def supplier(self,branch):
        pool=self.suppliers.setdefault(branch.pk,[])
        if not pool:pool.extend(f'{DOT}{n}' for n in self.rng.sample(SUPPLIERS,6))
        return self.rng.choice(pool)

    def walk_in_seller(self):
        return f"{DOT}Walk-in seller {self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    # ------------------------------------------------------------------ stock
    def model_pick(self):
        row=self.rng.choice(PHONES);brand,model,storage,colour,kind,cond,cost=row
        return dict(brand=brand,model=model,storage=storage,colour=colour,kind=kind,condition=cond,cost=round_to(D(cost)*D(self.rng.uniform(.94,1.07)),500))

    def bulk_purchase(self,branch,day,size):
        supplier=self.supplier(branch);rows=[];total=D(0)
        for _ in range(size):
            if self.rng.random()<.6 and rows:pick=dict(self.rng.choice(rows)['_pick']);qty=self.rng.choice([1,2,3,4])        # several units of one configuration
            else:pick=self.model_pick();qty=self.rng.choice([1,1,2,3,5])
            lines=[]
            for _u in range(qty):
                n=luhn_imei(self.rng,self.used)
                lines.append(n+(' '+luhn_imei(self.rng,self.used) if self.rng.random()<.35 else ''))       # a third of the phones are dual-IMEI
            unit=round_to(pick['cost']*D(self.rng.uniform(.97,1.04)),500)
            rows.append(dict(_pick=pick,brand=pick['brand'],model=pick['model'],storage=pick['storage'],colour=pick['colour'],quantity=qty,condition=pick['condition'],device_type=pick['kind'],
                             grade=self.rng.choice(['',8,9,10]) if pick['kind']!='New' else '',pta_status=self.rng.choice(['PTA','PTA','PTA','Non-PTA']),cost=str(unit),asking_price=str(round_to(unit*D('1.12'),500)),cost_mode='each',imeis='\n'.join(lines),
                             notes=self.rng.choice(['','','battery changed','display replaced','box and charger included','minor scratches on frame'])));total+=unit*qty
            if sum(r['quantity'] for r in rows)>=size:break
        status=self.rng.choices(['full','part','unpaid'],[.5,.3,.2])[0];method=self.rng.choices(METHODS,[.55,.25,.08,.08,.04])[0]
        paid=D(0) if status=='unpaid' else (total if status=='full' else round_to(total*D(self.rng.uniform(.3,.7)),1000))
        self.ensure_funds(branch,method,paid,day)
        clean=[{k:v for k,v in r.items() if k!='_pick'} for r in rows]
        parsed=bulk.parse_rows(json.dumps(clean))
        data=dict(branch=branch,party_name=supplier,warehouse=self.wh(branch),posting_date=day,staff=self.person(branch),handler=self.person(branch),payment_status=status,amount_paid=paid if status=='part' else None,method=method,notes=self.note)
        bill=bulk.post(data,parsed,self.owner)
        if status!='full':self.queue_supplier_payment(branch,bill,day)
        self.counts['phones bought in bulk']+=sum(r['quantity'] for r in clean);return bill

    def single_purchase(self,branch,day,from_customer=False):
        pick=self.model_pick();n=luhn_imei(self.rng,self.used);status=self.rng.choice(['full','full','part','unpaid']);method=self.rng.choice(['Cash','Cash','Bank Transfer','JazzCash'])
        price=pick['cost'];paid=D(0) if status=='unpaid' else (price if status=='full' else round_to(price*D('.5'),1000));self.ensure_funds(branch,method,paid,day)
        data=dict(branch=branch,party_name=self.walk_in_seller() if from_customer else self.supplier(branch),variant=catalog_entry.check(pick),quantity=1,price=price,pta_status=self.rng.choice(['PTA','PTA','Non-PTA']),device_type=pick['kind'],condition=pick['condition'],
                  warehouse=self.wh(branch),staff=self.person(branch),handler=self.person(branch),posting_date=day,payment_status=status,amount_paid=paid if status=='part' else None,method=method,imeis=[[n]],notes=self.note,
                  phone_notes=self.rng.choice(['','','battery replaced','back glass changed']))
        bill=easy_retail.purchase(data,self.owner)
        if status!='full':self.queue_supplier_payment(branch,bill,day)
        self.counts['phones bought singly']+=1;return bill

    def restock_accessories(self,branch,day):
        if not self.accessory_products:self.make_accessories()
        supplier=f'{DOT}Gulberg Accessories House';items=[]
        for product,(name,low,high) in zip(self.accessory_products,ACCESSORIES):
            if self.rng.random()<.8:items.append((product,self.rng.choice([10,20,30,50]),round_to(D(low)*D(self.rng.uniform(.9,1.1)),10)))
        total=sum(q*c for _,q,c in items);status=self.rng.choice(['full','part','unpaid']);method='Cash'
        paid=D(0) if status=='unpaid' else (total if status=='full' else round_to(total/2,100));self.ensure_funds(branch,method,paid,day)
        supplier_party=customer_names.resolve(supplier,self.owner,kind='Supplier',branch=branch)
        receipt=s.new_doc('Purchase Receipt',branch,supplier=supplier_party,warehouse=self.wh(branch),buyer=self.person(branch),posting_date=day,notes=self.note)
        for product,qty,cost in items:Line.objects.create(document=receipt,product=product,warehouse=self.wh(branch),quantity=qty,rate=cost)
        receipt=s.submit(receipt,self.owner);bill=s.supplier_bill(receipt,self.owner);bill.posting_date=day;bill.save();bill=s.submit(bill,self.owner)
        if paid:s.submit(s.new_doc('Payment Entry',branch,supplier=supplier_party,reference=bill,handler=self.person(branch),posting_date=day,data=dict(direction='Pay',amount=str(paid),mode=method,reference='')),self.owner)
        if status!='full':self.queue_supplier_payment(branch,bill,day)
        self.counts['accessory restocks']+=1;return bill

    def make_accessories(self):
        for name,low,high in ACCESSORIES:
            code='TEST-ACC-'+''.join(w[0] for w in name.split()[:3]).upper()+str(len(self.accessory_products))
            p,_=Product.objects.get_or_create(code=code,defaults=dict(name=name,group='Accessories',serialized=False,stock_item=True,selling_price=D(high),reorder_level=D(8)))
            self.accessory_products.append(p)

    # ------------------------------------------------------------------ selling
    def stock(self,branch,day):
        return list(Device.objects.filter(branch=branch,status='Available',received_on__lte=day,warehouse__isnull=False).select_related('product','branch'))

    def sale(self,branch,day,devices=None,accessories=True,exchange_ready=False):
        stock=self.stock(branch,day)
        if not stock:return None
        picks=devices or self.rng.sample(stock,min(len(stock),self.rng.choices([1,2,3],[.8,.15,.05])[0]))
        c=self.customer(branch,day);items=[];total=D(0)
        for d in picks:
            margin=D(self.rng.uniform(.06,.24)) if d.device_type!='New' else D(self.rng.uniform(.04,.1))
            price=max(round_to(d.purchase_cost*(1+margin),500),d.purchase_cost+D(500));items.append(dict(product=d.product,device=d,qty=1,price=price,warranty=self.rng.choice([0,7,30,90,180,365])));total+=price
        if accessories and self.rng.random()<.45 and self.accessory_products:
            p=self.rng.choice(self.accessory_products);avail=s.on_hand(p,self.wh(branch))[0]
            if avail>=2:
                q=self.rng.choice([1,1,2]);price=round_to(p.selling_price*D(self.rng.uniform(.85,1.05)),50);items.append(dict(product=p,device=None,qty=q,price=price));total+=price*q
        profile=self.rng.choices(['cash','bank','part','credit'],[.55,.17,.17,.11])[0]
        if profile in('cash','bank'):pay=[(self.rng.choice(['Cash'] if profile=='cash' else ['Bank Transfer','JazzCash','Easypaisa','Card']),total)]
        elif profile=='part':
            first=round_to(total*D(self.rng.uniform(.3,.7)),500);pay=[('Cash',first)]
            if self.rng.random()<.3:pay=[('Cash',round_to(first/2,500)),(self.rng.choice(['JazzCash','Easypaisa']),first-round_to(first/2,500))]
        else:pay=[]
        paid=sum((a for _,a in pay),D(0))
        me=self.person(branch);customer=customer_names.resolve(c['name'],self.owner,branch=branch)
        if c['phone'] and not customer.phone:customer.phone=c['phone'];customer.save(update_fields=['phone','modified'])
        doc=s.new_doc('Sales Invoice',branch,customer=customer,seller=me,posting_date=day,notes=self.note)
        warranty=[]
        for it in items:
            if it['device']:
                dev=Device.objects.select_for_update().get(pk=it['device'].pk);dev.warranty_days=it['warranty'];dev.save(update_fields=['warranty_days','modified']);warranty.append(dict(phone=it['product'].name,device=dev.code,days=it['warranty']))
            Line.objects.create(document=doc,product=it['product'],device=it['device'],warehouse=self.wh(branch),quantity=it['qty'],rate=it['price'])
        doc.data['warranty']=warranty;doc.save()
        payments=[dict(mode=m,amount=str(a)) for m,a in pay if a>0]
        for m,a in pay:self.ensure_funds(branch,m,D(0),day)
        sale=s.checkout(doc,self.owner,payments,me)
        if sale.outstanding>0:
            due=day+timedelta(days=self.rng.choice([14,21,30,45]))
            if self.rng.random()<.25:credit.plan(sale,self.owner,self.rng.choice([2,3]),due)
            else:credit.plan(sale,self.owner,0,due)
            self.queue_collection(branch,sale,day)
        self.counts['sales']+=1;self.counts['sales: '+profile]+=1
        self.sold_log.append((branch.pk,sale.pk,day,[i['device'].pk for i in items if i['device']]));return sale

    # ------------------------------------------------------------------ money later
    def queue_collection(self,branch,sale,day):
        due=self.rng.randint(5,50);heapq.heappush(self.queue,((day+timedelta(days=due)),next(self.tiebreak),'collect',branch.pk,sale.pk))
    def queue_supplier_payment(self,branch,bill,day):
        heapq.heappush(self.queue,((day+timedelta(days=self.rng.randint(7,40))),next(self.tiebreak),'pay',branch.pk,bill.pk))

    def run_queue(self,day):
        while self.queue and self.queue[0][0]<=day:
            when,_,kind,bid,pk=heapq.heappop(self.queue);branch=Branch.objects.get(pk=bid);doc=Document.objects.get(pk=pk)
            if doc.status!='Submitted' or doc.outstanding<=0:continue
            part=doc.outstanding if self.rng.random()<.6 else round_to(doc.outstanding*D(self.rng.uniform(.3,.8)),500)
            part=min(max(part,D(500)),doc.outstanding)
            if kind=='collect':
                party=doc.customer;method=self.rng.choice(['Cash','Cash','Bank Transfer','JazzCash','Easypaisa'])
                self.attempt('customer payment',money.record_party_payment,self.owner,party,part,method,when,'',self.note)
                Document.objects.get(pk=pk).outstanding>0 and self.rng.random()<.5 and heapq.heappush(self.queue,(when+timedelta(days=self.rng.randint(10,25)),next(self.tiebreak),'collect',bid,pk))
            else:
                method=self.rng.choice(['Cash','Bank Transfer']);self.ensure_funds(branch,method,part,when)
                self.attempt('supplier payment',money.record_party_payment,self.owner,doc.supplier,part,method,when,'',self.note)
                Document.objects.get(pk=pk).outstanding>0 and self.rng.random()<.5 and heapq.heappush(self.queue,(when+timedelta(days=self.rng.randint(10,25)),next(self.tiebreak),'pay',bid,pk))

    # ------------------------------------------------------------------ the rest of the business
    def expenses(self,branch,day):
        for code,name,(lo,hi),when,dom in EXPENSES:
            hit=(when=='monthly' and day.day==dom) or (when=='random' and self.rng.random()<.16)
            if not hit:continue
            amount=round_to(D(self.rng.uniform(lo,hi)),100 if when=='random' else 500)
            if code=='COA-6200' and when=='monthly':
                for i in range(self.rng.choice([2,3])):
                    a=round_to(D(self.rng.uniform(lo,hi)),500);self.ensure_funds(branch,'Cash',a,day);self.attempt('expense',money.record_voucher,self.owner,branch,'expense',self.account(code),a,'Cash',day,f'Salary — staff member {i+1} {self.note}')
                continue
            method=self.rng.choice(['Cash','Cash','Bank Transfer']) if amount>20000 else 'Cash';self.ensure_funds(branch,method,amount,day)
            self.attempt('expense',money.record_voucher,self.owner,branch,'expense',self.account(code),amount,method,day,f'{name} {self.note}')

    def bank_activity(self,branch,day):
        if self.rng.random()<.12:
            amt=round_to(D(self.rng.uniform(100000,400000)),10000);self.ensure_funds(branch,'Cash',amt+D(100000),day);self.attempt('deposit',money.move_money,self.owner,branch,'Cash','Bank Transfer',amt,day,'','Cash deposited '+self.note)
        if self.rng.random()<.05:
            amt=round_to(D(self.rng.uniform(50000,150000)),10000)
            if self.cash(branch,'Bank Transfer')>=amt:self.attempt('withdrawal',money.move_money,self.owner,branch,'Bank Transfer','Cash',amt,day,'','Cash withdrawn '+self.note)
        if self.rng.random()<.03:
            amt=round_to(D(self.rng.uniform(20000,80000)),5000);self.ensure_funds(branch,'Cash',amt,day);self.attempt('drawings',money.record_voucher,self.owner,branch,'drawings',self.account('COA-3300'),amt,'Cash',day,'Owner drawings '+self.note)

    def returns_and_refunds(self,branch,day):
        recent=[x for x in self.sold_log if x[0]==branch.pk and 3<=(day-x[2]).days<=20 and x[3]]
        if not recent or self.rng.random()>.08:return
        _,pk,sold_on,ids=self.rng.choice(recent);sale=Document.objects.get(pk=pk)
        if sale.status!='Submitted' or sale.is_return:return
        dev=Device.objects.get(pk=ids[0])
        if dev.status!='Sold':return
        draft=self.attempt('sales return',returns.create,sale,self.owner,[str(dev.pk)],'Returned in working order, all accessories present',self.rng.choice(['Customer changed mind','Screen flicker after a week','Battery drains fast','Wrong colour','Customer not satisfied with camera']),warehouse=self.wh(branch))
        if not draft:return
        draft.posting_date=max(day,sale.posting_date);draft.save()
        done=self.attempt('sales return post',s.submit,draft,self.owner)
        if not done:return
        credit_left=-done.outstanding
        if credit_left>0:
            if sale.outstanding>0:self.attempt('apply return credit',returns.apply_credit,done,min(credit_left,Document.objects.get(pk=sale.pk).outstanding),self.owner)
            done.refresh_from_db()
            if -done.outstanding>0:self.ensure_funds(branch,'Cash',-done.outstanding,day);self.attempt('customer refund',money.record_party_cash_back,self.owner,sale.customer,-done.outstanding,'Cash',day,'','Refund '+self.note)
        self.counts['returns']+=1
        if self.rng.random()<.7:
            dev.refresh_from_db()
            if dev.status=='Holding':
                dev.status='Available';dev.inspection_status='Passed';dev.inspection_date=day;dev.save();self.counts['returned phones accepted']+=1

    def purchase_return(self,branch,day):
        if self.rng.random()>.06:return
        candidates=[(Document.objects.filter(pk=pk).first(),pk) for pk in [b[1] for b in self.bill_log if b[0]==branch.pk]]
        rng_bills=[d for d,_ in candidates if d and d.status=='Submitted' and not d.is_return and d.reference_id]
        if not rng_bills:return
        bill=self.rng.choice(rng_bills);lines=[l for l in bill.reference.lines.filter(device__isnull=False).select_related('device') if l.device.status=='Available' and l.device.branch_id==branch.pk]
        if not lines:return
        l=self.rng.choice(lines);self.attempt('purchase return',purchase_returns.create,bill,self.owner,[str(l.device_id)],{},self.rng.choice(['Dead on arrival','IMEI mismatch with box','Supplier sent wrong colour','Battery health below agreed']));self.counts['purchase returns attempted']+=1

    def exchanges(self,branch,day):
        if self.rng.random()>.035:return
        stock=self.stock(branch,day)
        if not stock:return
        phone=self.rng.choice(stock);old=self.model_pick();old_value=round_to(old['cost']*D('.8'),500);price=max(round_to(phone.purchase_cost*D('1.2'),500),old_value+D(5000));me=self.person(branch);n=luhn_imei(self.rng,self.used)
        variant=catalog_entry.resolve_variant(old)
        data=dict(branch=branch,party_name=self.customer(branch,day)['name'],phone=phone,pta_status='PTA',price=price,posting_date=day,staff=me,buyer=me,handler=me,warehouse=self.wh(branch),method='Cash',notes=self.note)
        self.ensure_funds(branch,'Cash',max(old_value-price,D(0))+D(1000),day)
        done=self.attempt('exchange',exchange.post,data,[dict(product=variant,value=old_value,imeis=[n],condition=old['condition'],grade=8,pta='PTA',notes='Trade-in '+self.note)],self.owner)
        if done:
            for dev in Device.objects.filter(imeis__number=n,status='Holding'):
                if self.rng.random()<.6:dev.status='Available';dev.inspection_status='Passed';dev.inspection_date=day;dev.save()

    def adjustments(self,branch,day):
        if self.rng.random()>.05:return
        stock=self.stock(branch,day)
        if stock and self.rng.random()<.5:
            self.attempt('stock adjustment',stock_adjust.post,dict(branch=branch,warehouse=self.wh(branch),reason=self.rng.choice(['Damaged','Lost / stolen']),phone=self.rng.choice(stock),posting_date=day,notes='Written off '+self.note),self.owner)
        elif self.accessory_products:
            p=self.rng.choice(self.accessory_products);have=s.on_hand(p,self.wh(branch))[0]
            if have>3:self.attempt('stock adjustment',stock_adjust.post,dict(branch=branch,warehouse=self.wh(branch),reason=self.rng.choice(['Damaged','Count correction','Lost / stolen']),product=p,change=-D(self.rng.choice([1,2,3])),posting_date=day,notes='Count '+self.note),self.owner)

    def cancellations(self,branch,day):
        if self.rng.random()>.04:return
        vouchers=list(Document.objects.filter(branch=branch,kind='Voucher',status='Submitted',notes__contains=self.note,posting_date__gte=day-timedelta(days=5)))
        if vouchers:self.attempt('cancel expense',s.cancel,self.rng.choice(vouchers),self.owner);self.counts['cancellations']+=1

    def transfers_cycle(self,day,index):
        a,b=self.shops
        source,target=(a,b) if index%2==0 else (b,a)
        stock=self.stock(source,day)
        if len(stock)<6:return
        picks=self.rng.sample(stock,self.rng.randint(2,5))
        doc=self.attempt('transfer create',transfers.create,self.owner,source,target,picks,'Balancing stock '+self.note)
        if not doc:return
        self.counts['transfers']+=1
        if index%7==3:self.attempt('transfer cancel',transfers.cancel,self.owner,doc,'Not needed');return
        if index%7==5:return                                              # left pending on purpose
        done=self.attempt('transfer dispatch',transfers.dispatch,self.owner,doc)
        if not done or index%7==6:return                                  # left in transit on purpose
        if index%5==1 and len(picks)>1:
            self.attempt('transfer receive (partial)',transfers.receive,self.owner,doc,[str(p.pk) for p in picks[:-1]])
            if index%2==0:self.attempt('transfer resolve',transfers.resolve,self.owner,doc,self.rng.choice(['return','write_off']),'Checked with courier '+self.note)
        else:self.attempt('transfer receive',transfers.receive,self.owner,doc)

    def accounting_extras(self):
        """The owner's advanced accounting: an asset with depreciation, a prepaid, an accrual and a loan, per shop."""
        for i,branch in enumerate(self.shops):
            start=self.today-timedelta(days=80)
            self.ensure_funds(branch,'Cash',D(400000),start)
            data=dict(name=f'{DOT}Display counter and racks',purchase_date=start,cost=D(180000+i*40000),salvage=D(15000),life_months=60,method='Straight line',rate=D(0),asset_account=str(self.account('COA-1510').pk),
                      accumulated_account=str(self.account('COA-1590').pk),expense_account=str(self.account('COA-6800').pk),funding='Cash')
            self.attempt('fixed asset',advanced.add_asset,self.owner,branch,data)
            self.attempt('prepaid rent',advanced.add_deferral,self.owner,branch,dict(kind='Prepaid',description=f'{DOT}Rent paid three months ahead',amount=D(270000),start_date=self.today-timedelta(days=62),months=3,expense_account=str(self.account('COA-6100').pk),balance_account=str(self.account('COA-1320').pk),mode='Cash'))
            self.attempt('accrual',advanced.add_deferral,self.owner,branch,dict(kind='Accrual',description=f'{DOT}Electricity used, bill not yet received',amount=D(41500),start_date=self.today-timedelta(days=20),reverse_on=self.today-timedelta(days=6),expense_account=str(self.account('COA-6300').pk),balance_account=str(self.account('COA-2300').pk)))
            self.attempt('loan',advanced.add_loan,self.owner,branch,dict(lender=f'{DOT}Family loan',principal=D(300000),annual_rate=D(0),start_date=self.today-timedelta(days=55),term_months=12,liability_account=str(self.account('COA-2500').pk),interest_account=str(self.account('COA-6900').pk),mode='Cash'))
            from .models import Loan,FixedAsset,Deferral
            loan=Loan.objects.filter(branch=branch,lender__startswith=DOT).first()
            if loan:self.attempt('loan repayment',advanced.repay_loan,self.owner,loan,self.today-timedelta(days=25),D(50000),D(0),'Cash')
            self.attempt('depreciation',advanced.depreciate_all,self.owner,branch,self.today)
            self.attempt('release prepaid / reverse accrual',advanced.process_deferrals,self.owner,branch,self.today)

    # ------------------------------------------------------------------ the whole story
    def run(self):
        coa.install_standard(self.owner);self.make_accessories()
        for branch in self.shops:
            for _ in range(2):self.ensure_funds(branch,'Cash',D(1_500_000),self.today-timedelta(days=self.days+1))
        start=self.today-timedelta(days=self.days-1);transfer_index=0
        for n in range(self.days):
            day=start+timedelta(days=n)
            for branch in self.shops:
                if n%6==0 or n==1:
                    bill=self.attempt('bulk purchase',self.bulk_purchase,branch,day,self.rng.choice([5,8,10,12,15,20,25]))
                    if bill:self.bill_log.append((branch.pk,bill.pk))
                if n%4==2:
                    bill=self.attempt('single purchase',self.single_purchase,branch,day,self.rng.random()<.3)
                    if bill:self.bill_log.append((branch.pk,bill.pk))
                if n%11==0:self.attempt('accessory restock',self.restock_accessories,branch,day)
                if n>=2:
                    for _ in range(self.rng.choice([1,2,3,4,5]) if day.weekday()!=4 else self.rng.choice([4,5,6,7])):self.attempt('sale',self.sale,branch,day)
                self.attempt('expenses',self.expenses,branch,day);self.attempt('bank activity',self.bank_activity,branch,day)
                self.returns_and_refunds(branch,day);self.purchase_return(branch,day);self.exchanges(branch,day);self.adjustments(branch,day);self.cancellations(branch,day)
            self.run_queue(day)
            if n>=10 and n%9==0:self.transfers_cycle(day,transfer_index);transfer_index+=1
        self.accounting_extras();return self.summary()

    def summary(self):
        marked=Party.objects.filter(name__startswith=DOT)
        return dict(counts=dict(self.counts),failures=self.failures[:80],customers=marked.filter(kind='Customer').count(),suppliers=marked.filter(kind='Supplier').count(),
                    products=Product.objects.count(),devices=Device.objects.count(),imeis=IMEI.objects.count(),documents=dict(Counter(Document.objects.values_list('kind',flat=True))))
