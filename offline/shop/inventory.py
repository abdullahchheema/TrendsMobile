"""Inventory: phones counted by kind. Identical phones (same model, storage, colour, PTA status, type and condition) are one line with
a stock count, so five identical phones read "5", not five rows. Open a line to see each phone (IMEI, grade, battery, price)."""
from django.db.models import Count,Max,Min,Q
from django.shortcuts import render
from . import access

STATUSES=(('Available','In stock'),('Holding','Holding (returned, to inspect)'),('Sold','Sold'),('In transit','In transit'),('','All'))


def listing(request):
    from .views import scoped_devices,paginate
    status=request.GET.get('status','Available');query=request.GET.get('search','').strip()
    objects=scoped_devices(request.user).select_related('product','branch')
    if status:objects=objects.filter(status=status)
    if query:
        digits=''.join(ch for ch in query if ch.isdigit())
        match=Q(product__name__icontains=query)|Q(product__brand__icontains=query)|Q(code__icontains=query)|Q(condition__icontains=query)
        if len(digits)>=4:match|=Q(imeis__number__contains=digits)
        objects=objects.filter(match).distinct()
    if request.GET.get('product'):       # open one line: the individual phones
        for key,field in (('product','product_id'),('shop','branch_id'),('type','device_type'),('pta','pta_status'),('condition','condition')):
            if request.GET.get(key):objects=objects.filter(**{field:request.GET[key]})
        phones=[dict(device=d,imeis=' / '.join(i.number for i in d.imeis.all())) for d in objects.prefetch_related('imeis').order_by('received_on','code')]
        first=phones[0]['device'] if phones else None
        page,pager_query=paginate(request,phones)
        return render(request,'inventory_units.html',dict(title=first.product.name if first else 'Phones',rows=page.object_list,page_obj=page,pager_query=pager_query,first=first,see_cost=access.costs(request.user),back=request.GET.get('back','')))
    groups=objects.values('product_id','product__name','branch_id','branch__name','device_type','pta_status','condition','status').annotate(count=Count('id',distinct=True),low=Min('asking_price'),high=Max('asking_price')).order_by('product__name','branch__name','condition')
    rows=[]
    for g in groups:
        rows.append(dict(name=g['product__name'],shop=g['branch__name'],kind=g['device_type'],pta=g['pta_status'],condition=g['condition'],status=g['status'],count=g['count'],low=g['low'],high=g['high'],
            query=f"product={g['product_id']}&shop={g['branch_id']}&type={g['device_type']}&pta={g['pta_status']}&condition={g['condition']}&status={g['status']}"))
    total=sum(r['count'] for r in rows)
    page,pager_query=paginate(request,rows)
    return render(request,'inventory.html',dict(title='Inventory',rows=page.object_list,page_obj=page,pager_query=pager_query,total=total,status=status,statuses=STATUSES,search=query))
