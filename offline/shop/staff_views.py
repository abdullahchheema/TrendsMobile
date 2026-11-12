"""Shop staff: the people who serve customers and buy stock (no login needed), their commission, and what each of them sold and bought.

* The owner adds a person to a shop. Every sale and purchase in that shop records who made it (POS, easy entry, bulk and accessory purchases).
* Once a sale or purchase is posted, only the owner can change who it is credited to.
* The owner sees each person's full record (every sale and purchase, with links) and the commission on their net sales."""
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
from .models import Branch,Document,Staff
from . import services as s

ZERO=Decimal('0.00')


def _owner(request):
    if not request.user.is_superuser:raise PermissionDenied('Only the owner manages staff and commission.')


def crew(branch):
    """The people without a login who make sales and purchases in this shop."""
    return Staff.objects.filter(branch=branch,enabled=True,crew=True)


def _rate(text):
    try:value=Decimal(str(text or '0').strip().replace(',',''))
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
                    Staff.objects.create(code=s.code('STF'),name=name,branch=branch,designation=request.POST.get('designation','').strip()[:100],commission_rate=_rate(request.POST.get('commission')),can_sell=True,can_buy=True,crew=True)
                    s.audit(request.user,'Staff added',None,branch=branch,staff=name);messages.success(request,f'{name} added to {branch.name}.')
                elif action=='update':
                    person=get_object_or_404(Staff,pk=request.POST.get('staff'),crew=True)
                    person.designation=request.POST.get('designation','').strip()[:100];person.commission_rate=_rate(request.POST.get('commission'));person.enabled=request.POST.get('enabled')=='on'
                    person.save();s.audit(request.user,'Staff updated',None,branch=person.branch,staff=person.name);messages.success(request,'Saved.')
                else:raise ValidationError('Unknown action.')
        except (ValidationError,IntegrityError) as error:
            from .views import error_text
            messages.error(request,error_text(error))
        return redirect('staff_list')
    people=[]
    for person in Staff.objects.filter(crew=True).select_related('branch').order_by('branch__name','name'):
        mine=Document.objects.filter(seller=person,kind__in=list(s.SELL),status='Submitted')
        people.append(dict(p=person,sales=mine.aggregate(t=Sum('total'))['t'] or ZERO,count=mine.filter(is_return=False).count()))
    return render(request,'staff_list.html',dict(title='Staff & Commission',people=people,shops=shops))


def _range(request):
    today=timezone.localdate()
    try:start=date.fromisoformat(request.GET['from']) if request.GET.get('from') else today.replace(day=1)
    except ValueError:start=today.replace(day=1)
    try:end=date.fromisoformat(request.GET['to']) if request.GET.get('to') else today
    except ValueError:end=today
    return start,end


@login_required
def staff_detail(request,pk):
    """One person's full record: every sale and purchase they made, what was sold net of returns, and the commission it earns."""
    _owner(request);person=get_object_or_404(Staff,pk=pk);start,end=_range(request)
    sales=list(Document.objects.filter(seller=person,kind__in=list(s.SELL),status='Submitted',posting_date__range=[start,end]).select_related('customer','branch').order_by('-posting_date','-created'))
    buys=list(Document.objects.filter(buyer=person,kind='Purchase Receipt',status='Submitted',posting_date__range=[start,end]).select_related('supplier','branch').order_by('-posting_date','-created'))
    net=sum((d.total for d in sales),ZERO);bought=sum((d.total for d in buys),ZERO)
    commission=(net*person.commission_rate/Decimal(100)).quantize(Decimal('0.01'))
    return render(request,'staff_detail.html',dict(title=person.name,person=person,sales=sales,buys=buys,net=net,bought=bought,commission=commission,start=start,end=end,
        sale_count=sum(1 for d in sales if not d.is_return),return_count=sum(1 for d in sales if d.is_return)))


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
