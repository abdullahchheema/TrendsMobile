"""Customer credit: payment due dates, instalment plans, credit limits, overdue list and reminder messages.

An invoice that is not paid in full can carry a due date and/or a schedule of instalments. Payments recorded against the
invoice (the normal customer payment flow) are applied to the instalments oldest-first, so "overdue" is always derived from
what has actually been paid; nothing is stored twice."""
from datetime import date,timedelta
from decimal import Decimal
from urllib.parse import quote
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import Document,Installment,Party
from . import access,permissions,services as s

ZERO=Decimal('0.00')


def split_evenly(total,count):
    cents=int(total*100);base,extra=divmod(cents,count)
    return [Decimal(base+(1 if i<extra else 0))/100 for i in range(count)]


def add_month(day,months):
    month=day.month-1+months;year=day.year+month//12;month=month%12+1
    last=[31,29 if year%4==0 and (year%100 or year%400==0) else 28,31,30,31,30,31,31,30,31,30,31][month-1]
    return date(year,month,min(day.day,last))


@transaction.atomic
def plan(doc,user,instalments,first_due):
    """Set the payment promise for an unpaid sale: one due date, or N monthly instalments starting on first_due."""
    doc=Document.objects.select_for_update().get(pk=doc.pk)
    if doc.kind not in s.SELL or doc.is_return or doc.status!='Submitted' or doc.outstanding<=0:raise ValidationError('Only a sale with an unpaid balance can have a payment plan.')
    access.require(user,doc.branch)
    if not (access.manager(user) or permissions.can(user,'payments') or permissions.can(user,'sell')):raise PermissionDenied('You cannot set payment terms.')
    instalments=int(instalments or 0)
    if instalments<0 or instalments>24:raise ValidationError('Use between 1 and 24 instalments.')
    if not first_due:raise ValidationError('Choose the date the customer will pay (or the first instalment date).')
    if first_due<doc.posting_date:raise ValidationError('The due date cannot be before the sale date.')
    doc.installments.all().delete()
    count=max(instalments,1)
    for number,(amount,due) in enumerate(zip(split_evenly(doc.outstanding,count),[add_month(first_due,i) for i in range(count)]),1):
        Installment.objects.create(document=doc,number=number,due_date=due,amount=amount)
    doc.due_date=add_month(first_due,count-1);doc.save()
    s.audit(user,'Set payment plan',doc,instalments=count,first_due=str(first_due),amount=str(doc.outstanding))
    return doc


def schedule(doc):
    """Instalments with what each still owes, applying payments oldest-first."""
    rows=list(doc.installments.order_by('number'))
    if not rows:return []
    planned=sum((r.amount for r in rows),ZERO);paid=max(planned-max(doc.outstanding,ZERO),ZERO)
    result=[]
    for r in rows:
        take=min(paid,r.amount);paid-=take
        result.append(dict(number=r.number,due_date=r.due_date,amount=r.amount,paid=take,left=r.amount-take))
    return result


def overdue_amount(doc,today=None):
    today=today or timezone.localdate()
    if doc.status!='Submitted' or doc.is_return or doc.outstanding<=0:return ZERO
    rows=schedule(doc)
    if rows:return sum((r['left'] for r in rows if r['due_date']<today),ZERO)
    return doc.outstanding if doc.due_date and doc.due_date<today else ZERO


def customer_balance(customer):
    return Document.objects.filter(customer=customer,status='Submitted',kind__in=list(s.SELL),is_return=False,outstanding__gt=0).aggregate(t=Sum('outstanding'))['t'] or ZERO


def check_limit(doc):
    """Refuse a sale that takes the customer above their credit limit unless a manager has approved it (override on the sale)."""
    if doc.data.get('exchange') or not doc.customer_id or doc.outstanding<=0:return
    limit=doc.customer.credit_limit
    if not limit or limit<=0:return
    balance=customer_balance(doc.customer)
    if balance>limit and not doc.data.get('override',{}).get('approved_by'):
        raise PermissionDenied(f'{doc.customer.name} would owe PKR {balance:,.2f}, above their credit limit of PKR {limit:,.2f}. A manager can approve it: enter their manager code and PIN under Manager override.')


def whatsapp_number(phone):
    digits=''.join(c for c in (phone or '') if c.isdigit())
    if digits.startswith('0092'):digits=digits[2:]
    if digits.startswith('0'):digits='92'+digits[1:]
    elif not digits.startswith('92') and len(digits)==10:digits='92'+digits
    return digits if len(digits)>=11 else ''


def reminder_text(customer,items,shop):
    lines=[f'Assalam o Alaikum {customer.name},','This is a gentle reminder from '+(shop or 'our shop')+' about your pending payment:']
    for it in items:lines.append(f'- Invoice {it["code"]}: PKR {it["overdue"]:,.0f} was due on {it["due"]:%d %b %Y}')
    lines+=[f'Total overdue: PKR {sum((i["overdue"] for i in items),ZERO):,.0f}','Please arrange payment at your earliest convenience. Thank you!']
    return '\n'.join(lines)


def overdue_groups(user,today=None):
    today=today or timezone.localdate()
    docs=Document.objects.filter(branch__in=access.branches(user),status='Submitted',kind__in=list(s.SELL),is_return=False,outstanding__gt=0).select_related('customer','branch').prefetch_related('installments')
    groups={}
    for d in docs:
        amount=overdue_amount(d,today)
        if not amount:continue
        rows=schedule(d);due=min((r['due_date'] for r in rows if r['left'] and r['due_date']<today),default=d.due_date)
        g=groups.setdefault(d.customer_id,dict(customer=d.customer,items=[],total=ZERO,shop=d.branch.name))
        g['items'].append(dict(document=d,code=d.code,overdue=amount,due=due,days=(today-due).days));g['total']+=amount
    for g in groups.values():
        text=reminder_text(g['customer'],g['items'],g['shop']);number=whatsapp_number(g['customer'].phone)
        g.update(message=text,whatsapp=f'https://wa.me/{number}?text={quote(text)}' if number else '',oldest=max(i['days'] for i in g['items']))
    return sorted(groups.values(),key=lambda g:-g['total'])


def _allowed(user):return access.manager(user) or permissions.can(user,'payments') or access.statements(user)


@login_required
def overdue(request):
    if not _allowed(request.user):raise PermissionDenied('You do not have access to this page.')
    groups=overdue_groups(request.user)
    return render(request,'overdue.html',dict(title='Overdue payments',groups=groups,total=sum((g['total'] for g in groups),ZERO)))


@login_required
def payment_plan(request,pk):
    from .views import document,error_text
    doc=document(request.user,pk)
    if doc.kind not in s.SELL or doc.is_return:return redirect('detail',pk=doc.pk)
    if request.method=='POST':
        try:
            raw=request.POST.get('first_due','').strip();first=date.fromisoformat(raw) if raw else None
            plan(doc,request.user,request.POST.get('instalments') or 0,first)
            messages.success(request,'Payment plan saved.');return redirect('detail',pk=doc.pk)
        except ValueError:messages.error(request,'Enter a valid date.')
        except ValidationError as error:messages.error(request,error_text(error))
    return render(request,'payment_plan.html',dict(title='Payment plan',doc=doc,schedule=schedule(doc),today=timezone.localdate(),overdue=overdue_amount(doc)))
