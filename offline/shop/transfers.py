"""Owner-only stock transfers between shops, with real statuses.

Pending     created; the phones are reserved (not for sale) and still counted in the sending shop.
Dispatched  the phones have left: they are In transit, out of the sending shop's stock and not in any shop yet.
Received    the owner confirmed the phones arrived: they are Available in the receiving shop.
Discrepancy some phones did not arrive; the owner resolves them (receive late, return to the sender, or write off).
Cancelled   stopped before arrival; the phones are back in the sending shop.
A phone is never available in two shops, and the books of both shops balance at every step."""
import uuid
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import Account,Branch,Device,Document,IMEI,LedgerEntry,Line,StockMovement
from . import access,setup,shopdefaults,services as s

ZERO=Decimal('0.00')
PENDING,DISPATCHED,RECEIVED,DISCREPANCY,CANCELLED='Pending','Dispatched','Received','Discrepancy','Cancelled'
STATES=[PENDING,DISPATCHED,DISCREPANCY,RECEIVED,CANCELLED]
OPEN_STATES=(PENDING,DISPATCHED,DISCREPANCY)


def _owner(user):
    if not user.is_superuser:raise PermissionDenied('Only the owner can manage stock transfers.')


def status_of(doc):
    """The transfer's status as shown to the owner. Old one-step transfers (state Completed) count as Received."""
    if doc.status=='Cancelled':return CANCELLED
    return RECEIVED if doc.state in ('Completed','') else doc.state


def transfers():
    return Document.objects.filter(kind='Stock Entry',data__has_key='transfer')


def _now():return timezone.localtime().isoformat(timespec='seconds')


def _devices(doc,statuses=None):
    ids=doc.data.get('devices',[])
    found=Device.objects.select_for_update().filter(pk__in=ids).select_related('product','branch')
    return [d for d in found if statuses is None or d.status in statuses]


def _post(doc,branch,on,debit,credit,amount):
    if amount>0:
        LedgerEntry.objects.create(document=doc,branch=branch,posting_date=on,account=debit,debit=amount)
        LedgerEntry.objects.create(document=doc,branch=branch,posting_date=on,account=credit,credit=amount)


def _open(branch,on):s.open_date(Document(branch=branch,posting_date=on))


def _lock(doc_pk):
    doc=Document.objects.select_for_update().select_related('branch').get(pk=doc_pk)
    if doc.kind!='Stock Entry' or not doc.data.get('transfer'):raise ValidationError('This is not a shop-to-shop transfer.')
    return doc


def _target(doc):return Branch.objects.get(pk=doc.data['target_branch'])


# ----------------------------------------------------------------------------- actions
@transaction.atomic
def create(user,source,target,devices,notes=''):
    """A new Pending transfer: the phones are reserved so they cannot be sold meanwhile."""
    _owner(user)
    if source.pk==target.pk:raise ValidationError('Choose a different shop to send the phones to.')
    if not devices:raise ValidationError('Choose at least one phone to send.')
    if len(devices)>200:raise ValidationError('Send at most 200 phones in one transfer.')
    locked=list(Device.objects.select_for_update().filter(pk__in=[d.pk for d in devices]).select_related('product'))
    if len(locked)!=len(set(d.pk for d in devices)):raise ValidationError('A phone could not be found.')
    for device in locked:
        if device.branch_id!=source.pk or device.status!='Available':raise ValidationError(f'{device.product.name} ({device.code}) is not available in {source.name}.')
    when=timezone.localdate();_open(source,when)
    doc=s.new_doc('Stock Entry',source,warehouse=shopdefaults.warehouse(source),posting_date=when,status='Submitted',state=PENDING,posted_by=user,notes=notes or f'Phones from {source.name} to {target.name}',
                  handler=shopdefaults.person(user,source),
                  data=dict(target_branch=str(target.pk),target_warehouse=str(shopdefaults.warehouse(target).pk),devices=[str(d.pk) for d in locked],received=[],missing=[],
                            transfer=dict(source_shop=source.name,source_shop_code=source.code,target_shop=target.name,target_shop_code=target.code,created_by=user.get_username(),created_at=_now())))
    for device in locked:
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=shopdefaults.warehouse(source),quantity=1,rate=0,cost=device.purchase_cost)
        device.status='Reserved';device.save(update_fields=['status','modified'])
    s.audit(user,'Transfer created',doc,branch=source,to=target.name,phones=len(locked));return doc


@transaction.atomic
def update_pending(user,doc,target,devices):
    """Change the destination or the phones of a transfer that has not been dispatched."""
    _owner(user);doc=_lock(doc.pk)
    if status_of(doc)!=PENDING:raise ValidationError('Only a pending transfer can be changed.')
    source=doc.branch
    if target.pk==source.pk:raise ValidationError('Choose a different shop to send the phones to.')
    if not devices:raise ValidationError('Choose at least one phone.')
    for old in _devices(doc):
        if old.status=='Reserved':old.status='Available';old.save(update_fields=['status','modified'])
    wanted=list(Device.objects.select_for_update().filter(pk__in=[d.pk for d in devices]).select_related('product'))
    for device in wanted:
        if device.branch_id!=source.pk or device.status!='Available':raise ValidationError(f'{device.product.name} ({device.code}) is not available in {source.name}.')
    doc.lines.all().delete()
    for device in wanted:
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=shopdefaults.warehouse(source),quantity=1,rate=0,cost=device.purchase_cost)
        device.status='Reserved';device.save(update_fields=['status','modified'])
    doc.data.update(target_branch=str(target.pk),target_warehouse=str(shopdefaults.warehouse(target).pk),devices=[str(d.pk) for d in wanted])
    doc.data['transfer'].update(target_shop=target.name,target_shop_code=target.code);doc.save()
    s.audit(user,'Transfer changed',doc,branch=source);return doc


@transaction.atomic
def dispatch(user,doc):
    """The phones leave the sending shop: they become In transit and their cost leaves that shop's stock."""
    _owner(user);doc=_lock(doc.pk)
    if status_of(doc)!=PENDING:raise ValidationError('Only a pending transfer can be dispatched.')
    source=doc.branch;on=timezone.localdate();_open(source,on)
    inventory,clearing=setup.transfer_accounts(source);warehouse=shopdefaults.warehouse(source);total=ZERO
    devices=_devices(doc)
    if not devices or any(d.status!='Reserved' for d in devices):raise ValidationError('Some phones are no longer reserved for this transfer. Cancel it and create a new one.')
    for device in devices:
        cost=device.purchase_cost;total+=cost
        StockMovement.objects.create(document=doc,posting_date=on,product=device.product,device=device,warehouse=warehouse,quantity=-1,value=-cost)
        device.status='In transit';device.warehouse=None;device.save(update_fields=['status','warehouse','modified'])
    _post(doc,source,on,clearing,inventory,total)
    doc.state=DISPATCHED;doc.total=total;doc.data['transfer'].update(dispatched_by=user.get_username(),dispatched_at=_now(),value=str(total));doc.save()
    s.audit(user,'Transfer dispatched',doc,branch=source,phones=len(devices),value=str(total));return doc


def _receive_devices(user,doc,devices):
    target=_target(doc);on=timezone.localdate();_open(target,on)
    inventory,clearing=setup.transfer_accounts(target);warehouse=shopdefaults.warehouse(target);total=ZERO
    for device in devices:
        cost=device.purchase_cost;total+=cost
        StockMovement.objects.create(document=doc,posting_date=on,product=device.product,device=device,warehouse=warehouse,quantity=1,value=cost)
        device.branch=target;device.warehouse=warehouse;device.status='Available';device.save(update_fields=['branch','warehouse','status','modified'])
    _post(doc,target,on,inventory,clearing,total);return total


@transaction.atomic
def receive(user,doc,received_ids=None):
    """The owner confirms which phones physically arrived. Missing ones make it a Discrepancy to resolve."""
    _owner(user);doc=_lock(doc.pk)
    if status_of(doc) not in (DISPATCHED,DISCREPANCY):raise ValidationError('Only a dispatched transfer can be received.')
    in_transit=_devices(doc,{'In transit'})
    chosen=in_transit if received_ids is None else [d for d in in_transit if str(d.pk) in {str(i) for i in received_ids}]
    if not chosen:raise ValidationError('Tick at least one phone that arrived.')
    _receive_devices(user,doc,chosen)
    doc.data['received']=sorted(set(doc.data.get('received',[]))|{str(d.pk) for d in chosen})
    missing=[str(d.pk) for d in in_transit if d not in chosen];doc.data['missing']=missing
    doc.state=DISCREPANCY if missing else RECEIVED
    doc.data['transfer'].update(received_by=user.get_username(),received_at=_now())
    if missing:doc.data['transfer']['discrepancy']=f'{len(missing)} phone(s) did not arrive'
    else:doc.data['transfer'].pop('discrepancy',None)
    doc.save();s.audit(user,'Transfer received' if not missing else 'Transfer received with discrepancy',doc,branch=_target(doc),received=len(chosen),missing=len(missing));return doc


def _return_to_source(doc,devices,on):
    source=doc.branch;_open(source,on)
    inventory,clearing=setup.transfer_accounts(source);warehouse=shopdefaults.warehouse(source);total=ZERO
    for device in devices:
        cost=device.purchase_cost;total+=cost
        StockMovement.objects.create(document=doc,posting_date=on,product=device.product,device=device,warehouse=warehouse,quantity=1,value=cost)
        device.branch=source;device.warehouse=warehouse;device.status='Available';device.save(update_fields=['branch','warehouse','status','modified'])
    _post(doc,source,on,inventory,clearing,total)


@transaction.atomic
def resolve(user,doc,action,note=''):
    """Settle phones that never arrived: 'return' them to the sending shop, or 'write_off' their cost as a stock loss."""
    _owner(user);doc=_lock(doc.pk)
    if status_of(doc)!=DISCREPANCY:raise ValidationError('This transfer has nothing to resolve.')
    left=_devices(doc,{'In transit'});on=timezone.localdate()
    if action=='return':_return_to_source(doc,left,on)
    elif action=='write_off':
        source=doc.branch;_open(source,on);_,clearing=setup.transfer_accounts(source)
        try:loss=Account.objects.get(code='COA-5200')
        except Account.DoesNotExist:raise ValidationError('Install the standard chart of accounts first (needed for the stock loss).')
        total=sum((d.purchase_cost for d in left),ZERO);_post(doc,source,on,loss,clearing,total)
        for device in left:
            device.status='Written off';device.warehouse=None;device.details=dict(device.details,written_off=dict(document=doc.code,reason='Lost in transfer'));device.save(update_fields=['status','warehouse','details','modified'])
    else:raise ValidationError('Choose what to do with the missing phones.')
    doc.state=RECEIVED;doc.data['missing']=[];doc.data['transfer'].update(resolution=action,resolution_note=note,resolved_by=user.get_username(),resolved_at=_now())
    doc.save();s.audit(user,'Transfer discrepancy resolved',doc,branch=doc.branch,resolution=action,phones=len(left));return doc


@transaction.atomic
def cancel(user,doc,reason=''):
    """Stop a transfer before anything has arrived. The phones are Available in the sending shop again."""
    _owner(user);doc=_lock(doc.pk);state=status_of(doc);source=doc.branch
    if state==PENDING:
        for device in _devices(doc):
            if device.status=='Reserved':device.status='Available';device.save(update_fields=['status','modified'])
    elif state==DISPATCHED:
        if doc.data.get('received'):raise ValidationError('Some phones have already arrived. Resolve the discrepancy instead.')
        _return_to_source(doc,_devices(doc,{'In transit'}),timezone.localdate())
    else:raise ValidationError('This transfer can no longer be cancelled.')
    doc.status='Cancelled';doc.state=CANCELLED;doc.data['transfer'].update(cancelled_by=user.get_username(),cancelled_at=_now(),cancel_reason=reason)
    doc.save();s.audit(user,'Transfer cancelled',doc,branch=source);return doc


def move_now(user,source,target,devices):
    """Create, dispatch and receive in one go (stock handed over in person)."""
    doc=create(user,source,target,devices);dispatch(user,doc);return receive(user,doc)


# ----------------------------------------------------------------------------- screens
def _imei_devices(text,source):
    numbers=[n for n in ' '.join(text.replace(',',' ').split()).split(' ') if n]
    found=[];missing=[]
    for number in numbers:
        match=IMEI.objects.filter(number=number,device__branch=source,device__status='Available').select_related('device__product').first()
        (found if match else missing).append(match.device if match else number)
    if missing:raise ValidationError('Not available in the sending shop: '+', '.join(missing))
    return found


def group_key(d):return f'{d.product_id}~{d.condition}~{d.pta_status}~{d.device_type}'


def stock_groups(source,q=''):
    """Available phones of a shop, identical ones (same model, condition, PTA status and type) in one row with a total quantity."""
    phones=Device.objects.filter(branch=source,status='Available').select_related('product').order_by('product__name','code')
    if q:phones=phones.filter(Q(code__icontains=q)|Q(product__name__icontains=q)|Q(imeis__number__icontains=q)).distinct()
    groups={}
    for d in phones:
        g=groups.setdefault(group_key(d),dict(key=group_key(d),name=d.product.name,condition=d.condition,pta=d.pta_status,kind=d.device_type,count=0,low=d.purchase_cost,high=d.purchase_cost))
        g['count']+=1;g['low']=min(g['low'],d.purchase_cost);g['high']=max(g['high'],d.purchase_cost)
    return list(groups.values())


def pick_by_quantity(source,keys,quantities):
    """The first N available phones of each chosen group (oldest first). Refuses a quantity larger than the stock."""
    chosen=[]
    for key,qty in zip(keys,quantities):
        qty=str(qty).strip()
        if not qty or qty=='0':continue
        try:n=int(qty)
        except ValueError:raise ValidationError('Quantity to transfer must be a whole number.')
        if n<0:raise ValidationError('Quantity to transfer cannot be negative.')
        try:product,condition,pta,kind=key.split('~',3)
        except ValueError:raise ValidationError('Unknown stock group.')
        rows=list(Device.objects.select_for_update().filter(branch=source,status='Available',product_id=product,condition=condition,pta_status=pta,device_type=kind).select_related('product').order_by('received_on','code'))
        if n>len(rows):raise ValidationError(f'Only {len(rows)} of {rows[0].product.name if rows else "that phone"} are available in {source.name}, not {n}.')
        chosen+=rows[:n]
    return chosen


def _run(request,make,done_message):
    from .views import error_text
    key=uuid.UUID(request.POST.get('request_key',''))
    payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
    with transaction.atomic():doc=s.once(key,request.user,{'transfer':payload},make)
    return doc


@login_required
def transfer(request):
    """All Transfers: filter by status, start a new one."""
    from .views import selected_branch,error_text
    _owner(request.user)
    source=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not source:return redirect('setup')
    targets=Branch.objects.filter(enabled=True).exclude(pk=source.pk).order_by('name')
    if request.method=='POST':
        try:
            def make():
                target=targets.get(pk=request.POST.get('target'))
                ids=request.POST.getlist('phone');phones=list(Device.objects.select_for_update().filter(pk__in=ids,branch=source,status='Available').select_related('product'))
                if len(phones)!=len(set(ids)):raise ValidationError('One of the phones is no longer available here. Reload and try again.')
                phones+=[d for d in pick_by_quantity(source,request.POST.getlist('group'),request.POST.getlist('qty')) if d.pk not in {p.pk for p in phones}]
                phones+= [d for d in _imei_devices(request.POST.get('imeis',''),source) if d.pk not in {p.pk for p in phones}]
                doc=create(request.user,source,target,phones,request.POST.get('notes','').strip())
                return dispatch(request.user,doc) if request.POST.get('mode')=='dispatch' else doc
            doc=_run(request,make,'')
            messages.success(request,f'Transfer {doc.code} {"dispatched" if status_of(doc)==DISPATCHED else "created (pending)"}: {doc.lines.count()} phone(s).')
            return redirect('transfer_detail',pk=doc.pk)
        except Branch.DoesNotExist:messages.error(request,'Choose the shop to send the phones to.')
        except (ValidationError,IntegrityError,ValueError) as error:messages.error(request,error_text(error))
    state=request.GET.get('state','')
    docs=transfers().select_related('branch').order_by('-created')
    rows=[dict(doc=d,status=status_of(d),t=d.data['transfer'],count=len(d.data.get('devices',[])) or d.lines.count()) for d in docs[:300]]
    if state:rows=[r for r in rows if r['status']==state or (state=='Open' and r['status'] in OPEN_STATES)]
    counts={name:sum(1 for d in transfers().exclude(status='Cancelled') if status_of(d)==name) for name in (PENDING,DISPATCHED,DISCREPANCY)}
    q=request.GET.get('q','').strip()
    groups=stock_groups(source,q);want=request.GET.get('want','')
    in_transit=Device.objects.filter(status='In transit').count()
    return render(request,'stock_transfer.html',dict(title='Stock Transfers',branch=source,targets=targets,groups=groups,want=want,q=q,request_key=uuid.uuid4(),rows=rows,state=state,states=STATES,counts=counts,in_transit=in_transit,show_costs=access.costs(request.user)))


@login_required
def detail(request,pk):
    from .views import error_text
    _owner(request.user);doc=get_object_or_404(transfers(),pk=pk);target=_target(doc)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            def make():
                if action=='dispatch':return dispatch(request.user,doc)
                if action=='cancel':return cancel(request.user,doc,request.POST.get('reason','').strip())
                if action=='receive':return receive(request.user,doc,request.POST.getlist('arrived'))
                if action in ('return','write_off'):return resolve(request.user,doc,action,request.POST.get('note','').strip())
                if action=='update':
                    ids=request.POST.getlist('phone');new_target=Branch.objects.filter(enabled=True).exclude(pk=doc.branch_id).get(pk=request.POST.get('target'))
                    phones=list(Device.objects.filter(pk__in=ids).select_related('product'))+_imei_devices(request.POST.get('imeis',''),doc.branch)
                    return update_pending(request.user,doc,new_target,phones)
                raise ValidationError('Unknown action.')
            _run(request,make,'')
            messages.success(request,{'dispatch':'Transfer dispatched. The phones are now in transit.','cancel':'Transfer cancelled. The phones are back in the sending shop.','receive':'Receipt recorded.','return':'Missing phones returned to the sending shop.','write_off':'Missing phones written off as a stock loss.','update':'Transfer changed.'}.get(action,'Done.'))
            return redirect('transfer_detail',pk=doc.pk)
        except (ValidationError,IntegrityError,ValueError,Branch.DoesNotExist) as error:messages.error(request,error_text(error) if not isinstance(error,Branch.DoesNotExist) else 'Choose a shop.')
        doc.refresh_from_db()
    status=status_of(doc)
    lines=[]
    ids=doc.data.get('devices') or [str(l.device_id) for l in doc.lines.all() if l.device_id]
    devices={str(d.pk):d for d in Device.objects.filter(pk__in=ids).select_related('product','branch').prefetch_related('imeis')}
    for i in ids:
        d=devices.get(i)
        if d:lines.append(dict(device=d,state='Arrived' if i in doc.data.get('received',[]) or (status==RECEIVED and d.branch_id==target.pk) else 'Missing' if i in doc.data.get('missing',[]) else d.status))
    editable=[d for d in Device.objects.filter(branch=doc.branch,status='Available').select_related('product').prefetch_related('imeis').order_by('product__name')[:300]] if status==PENDING else []
    return render(request,'transfer_detail.html',dict(title=doc.code,doc=doc,t=doc.data['transfer'],status=status,lines=lines,target=target,targets=Branch.objects.filter(enabled=True).exclude(pk=doc.branch_id).order_by('name'),editable=editable,request_key=uuid.uuid4(),show_costs=access.costs(request.user)))


@login_required
def slip(request,pk):
    _owner(request.user)
    doc=Document.objects.select_related('branch').filter(pk=pk,kind='Stock Entry').first()
    if not doc or not doc.data.get('transfer'):raise Http404
    t=doc.data['transfer'];ids=doc.data.get('devices') or []
    lines=list(doc.lines.select_related('product','device').prefetch_related('device__imeis'))
    return render(request,'transfer_slip.html',dict(doc=doc,t=t,status=status_of(doc),lines=lines,show_costs=access.costs(request.user)))
