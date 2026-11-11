"""Read-only view of the phones available in every shop, so a shop can point a customer to another shop.
It shows what is where (model, storage, colour, condition, how many, and the shop's phone and address) and nothing sensitive:
no IMEIs, no costs, no suppliers and no way to sell or open another shop's records."""
from django.contrib.auth.decorators import login_required
from django.db.models import Count,Q
from django.shortcuts import render
from .models import Branch,Device,ShopSettings
from . import access


@login_required
def stock_everywhere(request):
    access.staff(request.user)
    q=request.GET.get('q','').strip();shop=request.GET.get('shop','')
    devices=Device.objects.filter(status='Available',branch__enabled=True)
    if q:devices=devices.filter(Q(product__name__icontains=q)|Q(product__storage__icontains=q)|Q(product__colour__icontains=q)|Q(product__model__brand__icontains=q)|Q(product__model__name__icontains=q))
    if shop:devices=devices.filter(branch_id=shop) if shop.replace('-','').isalnum() else devices.none()
    mine=set(access.branches(request.user).values_list('pk',flat=True))
    contacts={c.branch_id:c for c in ShopSettings.objects.all()}
    rows=[]
    for r in devices.values('branch_id','branch__name','product__name','product__storage','product__colour','condition').annotate(n=Count('pk')).order_by('product__name','product__storage','branch__name'):
        contact=contacts.get(r['branch_id'])
        rows.append(dict(shop_id=r['branch_id'],shop=r['branch__name'],mine=r['branch_id'] in mine and not request.user.is_superuser,name=r['product__name'],storage=r['product__storage'],colour=r['product__colour'],condition=r['condition'],count=r['n'],phone=contact.shop_phone if contact else '',address=contact.shop_address if contact else ''))
    shops=Branch.objects.filter(enabled=True,is_online=False).order_by('name')
    return render(request,'stock_everywhere.html',dict(title='Stock in All Shops',rows=rows,q=q,shop=shop,all_shops=shops,total=sum(r['count'] for r in rows)))
