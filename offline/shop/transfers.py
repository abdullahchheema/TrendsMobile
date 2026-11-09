"""Send phones from this shop to another shop in one step, and print a slip that names the shop they came from."""
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect,render
from django.utils import timezone
from .models import Branch,Device,Document,Line
from . import access,shopdefaults,services as s


def send(user,source,target,devices,when=None):
    """Move the chosen phones from `source` to `target` as one posted transfer. Returns the transfer document."""
    if not user.is_superuser:raise PermissionDenied('Only the owner can send stock between shops.')
    if source.pk==target.pk:raise ValidationError('Choose a different shop to send the phones to.')
    if not devices:raise ValidationError('Tick at least one phone to send.')
    if len(devices)>100:raise ValidationError('Send at most 100 phones at a time.')
    doc=s.new_doc('Stock Entry',source,warehouse=shopdefaults.warehouse(source),posting_date=when or timezone.localdate(),notes=f'Phones sent from {source.name} to {target.name}',
                  data={'target_warehouse':str(shopdefaults.warehouse(target).pk)},handler=shopdefaults.person(user,source))
    for device in devices:Line.objects.create(document=doc,product=device.product,device=device,quantity=1,rate=0)
    return s.submit(doc,user)


@login_required
def transfer(request):
    from .views import selected_branch,error_text
    if not request.user.is_superuser:raise PermissionDenied('Only the owner can manage stock transfers.')
    source=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not source:return redirect('setup')
    targets=Branch.objects.filter(enabled=True).exclude(pk=source.pk).order_by('name')
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                target=targets.get(pk=request.POST.get('target'))
                phones=list(Device.objects.select_for_update().filter(pk__in=request.POST.getlist('phone'),branch=source,status='Available').select_related('product'))
                if len(phones)!=len(set(request.POST.getlist('phone'))):raise ValidationError('One of the phones is no longer available here. Reload and try again.')
                return send(request.user,source,target,phones)
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            with transaction.atomic():doc=s.once(key,request.user,{'transfer':payload},perform)
            messages.success(request,f'{doc.lines.count()} phone(s) sent. Print the slip and hand it over with the phones.')
            return redirect('transfer_slip',pk=doc.pk)
        except Branch.DoesNotExist:messages.error(request,'Choose the shop to send the phones to.')
        except (ValidationError,IntegrityError,ValueError) as error:messages.error(request,error_text(error))
    q=request.GET.get('q','').strip()
    phones=Device.objects.filter(branch=source,status='Available').select_related('product__model').prefetch_related('imeis').order_by('product__name','code')
    if q:phones=phones.filter(Q(code__icontains=q)|Q(product__name__icontains=q)|Q(imeis__number__icontains=q)).distinct()
    recent=Document.objects.filter(kind='Stock Entry',status='Submitted',data__has_key='transfer').filter(Q(branch=source)|Q(data__target_branch=str(source.pk))).select_related('branch').order_by('-posting_date','-created')[:15]
    return render(request,'stock_transfer.html',dict(title='Send phones to another shop',branch=source,targets=targets,phones=phones[:300],q=q,request_key=uuid.uuid4(),recent=recent,show_costs=access.costs(request.user)))


@login_required
def slip(request,pk):
    if not request.user.is_superuser:raise PermissionDenied('Only the owner can see transfer slips.')
    doc=Document.objects.select_related('branch').filter(pk=pk,kind='Stock Entry',status='Submitted').first()
    if not doc or not doc.data.get('transfer'):raise Http404
    t=doc.data['transfer'];lines=list(doc.lines.select_related('product','device').prefetch_related('device__imeis'))
    return render(request,'transfer_slip.html',dict(doc=doc,t=t,lines=lines,show_costs=access.costs(request.user)))
