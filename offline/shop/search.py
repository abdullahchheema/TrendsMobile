"""Global search (Ctrl/Cmd-K palette): phones by IMEI/code/model, customers and suppliers, and documents by reference.
Results are limited to what the signed-in user may already see (shop-scoped devices and documents)."""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from .models import Document,Party
from . import access

LIMIT=6


@login_required
def global_search(request):
    from .views import scoped,scoped_devices
    q=(request.GET.get('q') or '').strip()[:60]
    if len(q)<2:return JsonResponse({'results':[]})
    try:access.staff(request.user)
    except Exception:return JsonResponse({'results':[]},status=403)
    results=[]
    phones=(scoped_devices(request.user).filter(Q(imeis__number__icontains=q)|Q(code__icontains=q)|Q(product__name__icontains=q)|Q(product__model__name__icontains=q))
            .select_related('product','branch').prefetch_related('imeis').distinct().order_by('-created')[:LIMIT])
    for d in phones:
        imei=', '.join(i.number for i in d.imeis.all())
        results.append(dict(kind='Phone',label=d.product.name,hint=' · '.join(x for x in (imei,d.status,d.branch.name) if x),href=reverse('device',args=[d.pk])))
    for p in Party.objects.filter(Q(name__icontains=q)|Q(phone__icontains=q)).order_by('name')[:LIMIT]:
        results.append(dict(kind=p.kind,label=p.name,hint=p.phone,href=reverse('collection',args=[p.kind])+'?search='+q))
    for doc in scoped(request.user).filter(code__icontains=q).order_by('-posting_date','-created')[:LIMIT]:
        results.append(dict(kind='Document',label=doc.code,hint=f'{doc.kind} · {doc.posting_date:%d %b %Y}',href=reverse('detail',args=[doc.pk])))
    return JsonResponse({'results':results})
