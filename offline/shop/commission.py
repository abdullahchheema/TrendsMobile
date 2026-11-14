"""Staff commission: what each person has earned, what the owner has paid them, and what is still to pay.

Earned   each sale earns its net amount (after discount, without tax) x the person's rate on the day of the sale. A return gives back exactly
         what its sale earned (at that sale's rate), and a cancelled sale earns nothing, so the figures follow the sales automatically.
Paid     the owner pays at any time: straight after a sale ("pay this sale") or once a month for everything pending. Each payment is a
         Voucher in the person's shop: Dr Commissions and incentives (COA-6750), Cr the cash or bank account it is paid from. Commission is
         therefore an expense when it is paid (owner's decision D2, 14 Nov 2026: earned on the sale date, reported, paid when the owner chooses).
Pending  earned less paid. A payment for one sale settles that sale first; everything else settles the oldest sales first. If a sale that
         was already paid comes back, the person has been paid ahead and the next sales absorb it (nothing more is paid until they do)."""
from datetime import date
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Account,Document,Staff
from . import pricing,services as s

ZERO=Decimal('0.00')
BEGINNING=date(2000,1,1)
EXPENSE_CODE='COA-6750'         # Commissions and incentives (standard chart)


def rate_table(person):
    """[(day, rate)] oldest first, for finding the rate in force on any date."""
    rows=list(person.rates.order_by('effective_from').values_list('effective_from','rate'))
    return rows or [(BEGINNING,person.commission_rate)]


def rate_on(table,day):
    found=table[0][1]
    for start,rate in table:
        if start<=day:found=rate
        else:break
    return found


def earned_rows(person,start=None,end=None):
    """Every posted sale and return credited to the person (newest first), with its net amount, the rate that applies and the commission."""
    docs=Document.objects.filter(seller=person,kind__in=list(s.SELL),status='Submitted').select_related('customer','branch','reference')
    if start:docs=docs.filter(posting_date__gte=start)
    if end:docs=docs.filter(posting_date__lte=end)
    table=rate_table(person);rows=[]
    for d in docs.order_by('-posting_date','-created'):
        earned_on=d.reference.posting_date if d.is_return and d.reference_id else d.posting_date      # a return gives back exactly what its sale earned
        amount=pricing.net_amount(d);rate=rate_on(table,earned_on)
        rows.append(dict(d=d,net=amount,rate=rate,earned=(amount*rate/Decimal(100)).quantize(Decimal('0.01')),sale_id=d.reference_id if d.is_return else d.pk))
    return rows


def payments(person,start=None,end=None):
    rows=Document.objects.filter(kind='Voucher',status='Submitted',data__commission_staff=str(person.pk)).select_related('branch')
    if start:rows=rows.filter(posting_date__gte=start)
    if end:rows=rows.filter(posting_date__lte=end)
    return list(rows.order_by('-posting_date','-created'))


def account(person):
    """All-time position: earned, paid, pending, and per sale what it earned (net of its returns), what has been paid against it and what is left."""
    rows=earned_rows(person);paid_docs=payments(person)
    per_sale={}
    for r in reversed(rows):                    # oldest first
        key=r['sale_id']
        if key not in per_sale:per_sale[key]=dict(sale_id=key,earned=ZERO,paid=ZERO,order=(r['d'].posting_date,r['d'].created))
        per_sale[key]['earned']+=r['earned']
        if not r['d'].is_return:per_sale[key]['order']=(r['d'].posting_date,r['d'].created)
    pool=ZERO
    for p in sorted(paid_docs,key=lambda d:(d.posting_date,d.created)):
        amount=p.total;target=per_sale.get(_uuid(p.data.get('commission_sale')))
        if target is not None:                  # paid for one sale: that sale first
            take=max(min(amount,target['earned']-target['paid']),ZERO);target['paid']+=take;amount-=take
        pool+=amount
    for item in sorted(per_sale.values(),key=lambda x:x['order']):
        take=max(min(pool,item['earned']-item['paid']),ZERO);item['paid']+=take;pool-=take
    for item in per_sale.values():item['pending']=item['earned']-item['paid']
    earned=sum((r['earned'] for r in rows),ZERO);paid=sum((p.total for p in paid_docs),ZERO)
    return dict(earned=earned,paid=paid,pending=earned-paid,sales=per_sale,payments=paid_docs)


def _uuid(value):
    import uuid
    try:return uuid.UUID(str(value)) if value else None
    except ValueError:return None


@transaction.atomic
def pay(user,person,amount,method,when=None,note='',sale=None,reference=''):
    """The owner pays commission to a person from the person's shop. `sale` (optional) is the sale this payment is for."""
    if not user.is_superuser:raise PermissionDenied('Only the owner pays staff commission.')
    person=Staff.objects.select_for_update(of=('self',)).select_related('branch').get(pk=person.pk)       # one payment at a time per person
    branch=person.branch
    if branch.is_online or not branch.enabled:raise ValidationError('Choose a person who works in an active shop.')
    amount=s.payment_money(amount);when=when or timezone.localdate()
    if amount<=0:raise ValidationError('Enter the amount to pay (more than zero).')
    if when>timezone.localdate():raise ValidationError('A payment cannot be dated in the future.')
    if sale is not None and (sale.seller_id!=person.pk or sale.status!='Submitted' or sale.is_return or sale.kind not in s.SELL):raise ValidationError('That sale was not made by this person.')
    now=account(person)
    if amount>now['pending']:
        raise ValidationError(f'{person.name} has PKR {max(now["pending"],ZERO):,.2f} of commission still to be paid, so PKR {amount:,.2f} cannot be paid.'+(' They have been paid ahead: a sale that was already paid for came back.' if now['pending']<0 else ''))
    if sale is not None:
        left=now['sales'].get(sale.pk,{}).get('pending',ZERO)
        if amount>left:raise ValidationError(f'The commission still to pay on {sale.code} is PKR {max(left,ZERO):,.2f}.')
    expense=Account.objects.filter(code=EXPENSE_CODE,enabled=True,is_group=False).first()
    if expense is None:
        from . import coa
        coa.install_standard(user);expense=Account.objects.get(code=EXPENSE_CODE)
    text=' '.join(str(note or '').split())[:200]
    description=f'Commission paid to {person.name}'+(f' for {sale.code}' if sale is not None else '')+(f' · {text}' if text else '')
    doc=s.new_doc('Voucher',branch,posting_date=when,notes=description,data=dict(type='expense',amount=str(amount),account=str(expense.pk),mode=method,reference=str(reference or '')[:180],
                  description=description,commission_staff=str(person.pk),commission_sale=str(sale.pk) if sale is not None else ''))
    doc=s.submit(doc,user)
    s.audit(user,'Commission paid',doc,branch=branch,staff=person.name,amount=str(amount),sale=sale.code if sale is not None else '')
    return doc
