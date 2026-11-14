"""Shop staff: the people who serve customers and buy stock (no login needed), their commission, and what each of them sold and bought.

* The owner adds a person to a shop. Every sale and purchase in that shop records who made it (POS, easy entry, bulk and accessory purchases).
* Once a sale or purchase is posted, only the owner can change who it is credited to.
* The owner sees each person's full record (every sale and purchase, with links), the commission they earned, what has been paid to them and
  what is still to pay, and pays it straight after a sale or once a month (see commission.py)."""
from datetime import date
from decimal import Decimal,InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Count,Sum
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Branch,Document,Staff,StaffRate
from . import commission as cm,pricing,services as s
from .commission import BEGINNING,rate_table,rate_on       # kept importable from here

ZERO=Decimal('0.00')


def set_rate(person,rate,today=None):
    """Change a person's commission from today on. Sales made before today keep the rate they were made under."""
    today=today or timezone.localdate()
    if not person.rates.exists():StaffRate.objects.create(staff=person,rate=person.commission_rate,effective_from=BEGINNING)
    if person.rates.order_by('-effective_from').values_list('rate',flat=True).first()==rate:return False
    StaffRate.objects.update_or_create(staff=person,effective_from=today,defaults=dict(rate=rate))
    person.commission_rate=rate;return True


def _owner(request):
    if not request.user.is_superuser:raise PermissionDenied('Only the owner manages staff and commission.')


def crew(branch):
    """The people without a login who make sales and purchases in this shop."""
    return Staff.objects.filter(branch=branch,enabled=True,crew=True)


def _rate(text):
    if not str(text or '').strip():raise ValidationError('Enter the commission percentage for this person (enter 0 if they earn none).')
    try:value=Decimal(str(text).strip().replace(',',''))
    except InvalidOperation:raise ValidationError('The commission must be a number.')
    if not Decimal(0)<=value<=Decimal(100):raise ValidationError('The commission must be between 0 and 100 percent.')
    return value


@login_required
def staff_list(request):
    _owner(request)
    shops=list(Branch.objects.filter(enabled=True,is_online=False).order_by('name'))
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            with transaction.atomic():
                if action=='add':
                    name=' '.join(request.POST.get('name','').split())[:180]
                    if not name:raise ValidationError('Enter the staff member’s name.')
                    branch=get_object_or_404(Branch,pk=request.POST.get('branch'),enabled=True,is_online=False)
                    if Staff.objects.filter(branch=branch,name__iexact=name).exists():raise ValidationError('That name is already used in this shop.')
                    rate=_rate(request.POST.get('commission'))
                    person=Staff.objects.create(code=s.code('STF'),name=name,branch=branch,designation=request.POST.get('designation','').strip()[:100],commission_rate=rate,can_sell=True,can_buy=True,crew=True)
                    StaffRate.objects.create(staff=person,rate=rate,effective_from=BEGINNING)
                    s.audit(request.user,'Staff added',None,branch=branch,staff=name);messages.success(request,f'{name} added to {branch.name}.')
                elif action=='update':
                    person=Staff.objects.select_for_update(of=('self',)).select_related('branch').get(pk=request.POST.get('staff'),crew=True)
                    rate=_rate(request.POST.get('commission'));old_shop=person.branch
                    person.designation=request.POST.get('designation','').strip()[:100];person.enabled=request.POST.get('enabled')=='on'
                    moved=request.POST.get('branch') and request.POST['branch']!=str(person.branch_id)
                    if moved:
                        target=get_object_or_404(Branch,pk=request.POST['branch'],enabled=True,is_online=False)
                        if Staff.objects.filter(branch=target,name__iexact=person.name).exclude(pk=person.pk).exists():raise ValidationError(f'{target.name} already has someone called {person.name}.')
                        person.branch=target;person.default_warehouse=None
                    changed=set_rate(person,rate)
                    person.full_clean(exclude=['code','user','role']);person.save()
                    note=(f' moved from {old_shop.name} to {person.branch.name}' if moved else '')+(f' commission now {rate}%' if changed else '')
                    s.audit(request.user,'Staff updated',None,branch=person.branch,staff=person.name,change=note.strip());messages.success(request,'Saved.'+(f' {person.name} is now in {person.branch.name}; past sales stay in {old_shop.name}.' if moved else '')+(' The new commission applies from today; earlier sales keep their old rate.' if changed else ''))
                else:raise ValidationError('Unknown action.')
        except (ValidationError,IntegrityError) as error:
            from .views import error_text
            messages.error(request,error_text(error))
        return redirect('staff_list')
    people=[]
    for person in Staff.objects.filter(crew=True).select_related('branch').order_by('branch__name','name'):
        mine=Document.objects.filter(seller=person,kind__in=list(s.SELL),status='Submitted')
        position=cm.account(person)
        people.append(dict(p=person,sales=sum((pricing.net_amount(d) for d in mine.only('total','data')),ZERO),count=mine.filter(is_return=False).count(),pending=position['pending'],paid=position['paid']))
    return render(request,'staff_list.html',dict(title='Staff & Commission',people=people,shops=shops,transfer_shops=shops))


def _range(request):
    today=timezone.localdate()
    try:start=date.fromisoformat(request.GET['from']) if request.GET.get('from') else today.replace(day=1)
    except ValueError:start=today.replace(day=1)
    try:end=date.fromisoformat(request.GET['to']) if request.GET.get('to') else today
    except ValueError:end=today
    return start,end


@login_required
def staff_detail(request,pk):
    """One person's full record: every sale and purchase they made, what was sold net of returns, the commission it earns, what has been paid and
    what is still to pay. The owner pays from here: one sale's commission straight away, or everything pending at the end of a month."""
    _owner(request);person=get_object_or_404(Staff.objects.select_related('branch'),pk=pk);start,end=_range(request)
    if request.method=='POST':return _pay(request,person)
    sale_rows=cm.earned_rows(person,start,end)             # net of discount and tax (the tax belongs to the government), at the rate in force on the day of the sale
    position=cm.account(person)
    for r in sale_rows:
        item=position['sales'].get(r['sale_id'],{})
        r['paid']=item.get('paid',ZERO) if not r['d'].is_return else None;r['pending']=item.get('pending',ZERO) if not r['d'].is_return else None
        r['returned']=not r['d'].is_return and item.get('earned',ZERO)<=0 and r['earned']>0          # the sale came back: it earns nothing now
    buys=list(Document.objects.filter(buyer=person,kind='Purchase Receipt',status='Submitted',posting_date__range=[start,end]).select_related('supplier','branch').order_by('-posting_date','-created'))
    net=sum((r['net'] for r in sale_rows),ZERO);commission=sum((r['earned'] for r in sale_rows),ZERO)
    period_ids={r['sale_id'] for r in sale_rows}
    period_pending=sum((max(position['sales'][i]['pending'],ZERO) for i in period_ids if i in position['sales']),ZERO)
    paid_in_period=cm.payments(person,start,end)
    from django.urls import reverse
    from .models import ShopSettings
    methods=list((ShopSettings.objects.filter(branch=person.branch).values_list('payment_methods',flat=True).first() or {}).keys())
    suggested=request.GET.get('pay') or (str(max(position['pending'],ZERO)) if position['pending']>0 else '')
    return render(request,'staff_detail.html',dict(title=person.name,person=person,sales=sale_rows,buys=buys,net=net,bought=sum((d.total for d in buys),ZERO),commission=commission,start=start,end=end,
        sale_count=sum(1 for r in sale_rows if not r['d'].is_return),return_count=sum(1 for r in sale_rows if r['d'].is_return),rates=[dict(day=a,rate=b) for a,b in reversed(rate_table(person))],
        position=position,paid_in_period=paid_in_period,paid_period_total=sum((p.total for p in paid_in_period),ZERO),period_pending=period_pending,methods=methods,
        suggested=suggested,pay_sale=request.GET.get('sale',''),today=timezone.localdate(),request_key=__import__('uuid').uuid4(),here=reverse('staff_detail',args=[person.pk])))


def _pay(request,person):
    """Record a commission payment (once per form, even if the button is pressed twice)."""
    import uuid
    from django.urls import reverse
    from .views import error_text
    from urllib.parse import urlencode
    back=reverse('staff_detail',args=[person.pk])+'?'+urlencode({k:v for k,v in request.GET.items() if k in ('from','to')})      # back to the plain form: the sale just paid is not offered again
    try:
        when=date.fromisoformat(request.POST.get('date') or str(timezone.localdate()))
        sale=None
        if request.POST.get('sale'):sale=get_object_or_404(Document,pk=request.POST['sale'],seller=person)
        key=uuid.UUID(request.POST.get('request_key',''))
        payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
        with transaction.atomic():
            doc=s.once(key,request.user,{'commission':str(person.pk),**payload},lambda:cm.pay(request.user,person,request.POST.get('amount'),request.POST.get('method'),when,request.POST.get('note',''),sale,request.POST.get('reference','')))
        messages.success(request,f'Commission of PKR {doc.total:,.2f} paid to {person.name} ({doc.code}). It is recorded as an expense of {person.branch.name}.')
    except ValueError:messages.error(request,'Enter a valid date.')
    except (ValidationError,IntegrityError) as error:messages.error(request,error_text(error))
    return redirect(back)


@login_required
@require_POST
def change_staff(request,pk):
    """After a sale or purchase is posted, only the owner can change the person it is credited to. Recorded in the audit log."""
    _owner(request)
    doc=get_object_or_404(Document,pk=pk,status='Submitted')
    field='seller' if doc.kind in s.SELL else 'buyer' if doc.kind=='Purchase Receipt' else None
    if field is None:raise PermissionDenied('Only sales and purchases record staff.')
    capability='can_sell' if field=='seller' else 'can_buy'
    try:
        person=get_object_or_404(Staff,pk=request.POST.get('staff'),branch=doc.branch,enabled=True)
        if not getattr(person,capability):raise ValidationError('That person is not allowed to do this in this shop.')
        before=getattr(doc,field)
        with transaction.atomic():
            setattr(doc,field,person);doc.staff_names=dict(doc.staff_names or {},**{field:person.name});doc.save(update_fields=[field,'staff_names','modified'])
            s.audit(request.user,'Staff changed on a posted document',doc,field=field,old=before.name if before else '',new=person.name)
        messages.success(request,f'Now credited to {person.name}.')
    except ValidationError as error:
        from .views import error_text
        messages.error(request,error_text(error))
    return redirect('detail',pk=doc.pk)
