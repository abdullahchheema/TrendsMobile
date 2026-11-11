"""Online Store (owner only): decide what the website shows, with photos, a description and an optional online price.

The website reads the read-only views `web_listings`, `web_stock`, `web_phone_units` and `web_images` (migration 0022). Nothing here changes
stock, prices in the shops or the books: the shop price (what the POS uses) and the online price are separate. A product appears on the
website only when it is Published, has at least one photo and a price."""
import json
from decimal import Decimal,InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Count,Max,Min,Q,Sum
from django.http import Http404,HttpResponse,JsonResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from .models import Branch,Device,Product,ProductImage,StockMovement
from . import product_media as media

ZERO=Decimal('0.00')


def _owner(request):
    if not request.user.is_superuser:raise PermissionDenied('Only the owner manages the online store.')


def make_slug(product):
    base=slugify(product.name)[:150] or 'product';slug=base;n=2
    while Product.objects.filter(slug=slug).exclude(pk=product.pk).exists():slug=f'{base}-{n}';n+=1
    return slug


def facts(products):
    """{product id: dict(stock, low, high, photos, cover)}: shop stock and shop-price range, plus photos, for the given products."""
    ids=[p.pk for p in products];out={p.pk:dict(stock=0,low=None,high=None,photos=0,cover='') for p in products}
    shops=Branch.objects.filter(enabled=True)
    for r in Device.objects.filter(product_id__in=ids,status='Available',branch__in=shops).values('product_id').annotate(n=Count('id'),lo=Min('asking_price'),hi=Max('asking_price')):
        out[r['product_id']].update(stock=r['n'],low=r['lo'],high=r['hi'])
    for r in StockMovement.objects.filter(product_id__in=ids,product__serialized=False,warehouse__branch__in=shops).values('product_id').annotate(q=Sum('quantity')):
        out[r['product_id']]['stock']=r['q'] or 0
    for img in ProductImage.objects.filter(product_id__in=ids).order_by('position','created'):
        f=out[img.product_id];f['photos']+=1
        if not f['cover']:f['cover']=img.url
    for p in products:
        if not p.serialized:out[p.pk]['low']=out[p.pk]['high']=p.selling_price
    return out


def status_of(product,info):
    if product.online_visible:return 'Published'
    return 'Draft' if info['photos'] else 'Needs photo'


def problems(product,photos,price):
    """Why a product cannot be published yet (empty list = fine)."""
    found=[]
    if not photos:found.append('add at least one photo')
    if not (price and price>0):found.append('set a price (the shop price or an online price)')
    return found


@login_required
def store(request):
    _owner(request)
    q=request.GET.get('q','').strip();flt=request.GET.get('show','')
    products=Product.objects.filter(enabled=True).filter(Q(serialized=True)|Q(stock_item=True)).select_related('model').order_by('group','name')
    if q:products=products.filter(Q(name__icontains=q)|Q(brand__icontains=q)|Q(model__name__icontains=q)|Q(code__icontains=q))
    products=list(products);info=facts(products);rows=[]
    for p in products:
        i=info[p.pk]
        if p.serialized and not i['stock'] and not p.online_visible and not i['photos'] and not flt=='all':continue     # variants that never had stock are noise
        rows.append(dict(p=p,i=i,status=status_of(p,i),online=p.online_price if p.online_price is not None else None))
    counts={'Published':sum(1 for r in rows if r['status']=='Published'),'Draft':sum(1 for r in rows if r['status']=='Draft'),'needs':sum(1 for r in rows if r['status']=='Needs photo')}
    if flt in ('Published','Draft','Needs photo'):rows=[r for r in rows if r['status']==flt]
    from .views import paginate
    page,pager=paginate(request,rows,40)
    return render(request,'online_store.html',dict(title='Online Store',rows=page.object_list,page_obj=page,pager_query=pager,q=q,show=flt,counts=counts))


def _payload(product):
    return [dict(id=str(i.pk),url=i.url,alt=i.alt) for i in product.images.all()]


@login_required
def edit(request,pk):
    _owner(request);product=get_object_or_404(Product.objects.select_related('model'),pk=pk)
    info=facts([product])[product.pk]
    if request.method=='POST':
        try:
            price_text=request.POST.get('online_price','').strip().replace(',','')
            try:price=Decimal(price_text) if price_text else None
            except InvalidOperation:raise ValidationError('Enter the online price as a number, or leave it empty to use the shop price.')
            if price is not None and price<=0:raise ValidationError('The online price must be more than zero, or empty to use the shop price.')
            publish=request.POST.get('online_visible')=='on'
            if publish:
                effective=price if price is not None else (info['low'] if product.serialized else product.selling_price)
                missing=problems(product,info['photos'],effective)
                if missing:raise ValidationError('Before publishing: '+' and '.join(missing)+'.')
            with transaction.atomic():
                product=Product.objects.select_for_update().get(pk=pk)
                product.description=request.POST.get('description','').strip()[:5000];product.online_price=price;product.online_visible=publish
                if publish and not product.slug:product.slug=make_slug(product)
                product.save(update_fields=['description','online_price','online_visible','slug','modified'])
            messages.success(request,'Saved. '+('This product is live on the website.' if publish else 'It is not shown on the website.'));return redirect('online_edit',pk=pk)
        except ValidationError as error:
            from .views import error_text
            messages.error(request,error_text(error))
    units=list(Device.objects.filter(product=product,status='Available',branch__enabled=True).select_related('branch').order_by('branch__name','code')[:200]) if product.serialized else []
    return render(request,'online_edit.html',dict(title=product.name,product=product,info=info,status=status_of(product,info),images=json.dumps(_payload(product)),units=units,
                                                 max_photos=media.MAX_PER_PRODUCT,max_mb=media.MAX_BYTES//1048576))


@login_required
@require_POST
def photo_upload(request,pk):
    _owner(request);product=get_object_or_404(Product,pk=pk)
    try:
        upload=request.FILES.get('file')
        if not upload:raise ValidationError('Choose a photo.')
        raw=upload.read(media.MAX_BYTES+1)
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=pk)
            count=ProductImage.objects.filter(product=product).count()
            if count>=media.MAX_PER_PRODUCT:raise ValidationError(f'A product can have at most {media.MAX_PER_PRODUCT} photos.')
            path,url,size=media.save(product.pk,raw)
            image=ProductImage.objects.create(product=product,path=path,url=url,size=size,position=(ProductImage.objects.filter(product=product).aggregate(m=Max('position'))['m'] or 0)+1 if count else 0,alt=product.name[:200])
        return JsonResponse(dict(id=str(image.pk),url=image.url,alt=image.alt))
    except ValidationError as error:
        from .views import error_text
        return JsonResponse({'error':error_text(error)},status=400)


@login_required
@require_POST
def photo_order(request,pk):
    _owner(request);product=get_object_or_404(Product,pk=pk)
    try:ids=[str(i) for i in json.loads(request.body)['order']]
    except (ValueError,KeyError,TypeError):return JsonResponse({'error':'Invalid order.'},status=400)
    with transaction.atomic():
        mine={str(i.pk):i for i in ProductImage.objects.select_for_update().filter(product=product)}
        if set(ids)!=set(mine):return JsonResponse({'error':'The photo list changed. Reload the page.'},status=409)
        for position,key in enumerate(ids):
            if mine[key].position!=position:ProductImage.objects.filter(pk=key).update(position=position)
    return JsonResponse({'ok':True})


@login_required
@require_POST
def photo_update(request,pk):
    _owner(request);image=get_object_or_404(ProductImage,pk=pk)
    try:alt=str(json.loads(request.body).get('alt',''))
    except (ValueError,AttributeError):return JsonResponse({'error':'Invalid request.'},status=400)
    image.alt=' '.join(alt.split())[:200];image.save(update_fields=['alt']);return JsonResponse({'ok':True})


@login_required
@require_POST
def photo_delete(request,pk):
    _owner(request);image=get_object_or_404(ProductImage,pk=pk);product=image.product
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=product.pk)
        path=image.path;image.delete()
        for position,i in enumerate(ProductImage.objects.filter(product=product).order_by('position','created')):
            if i.position!=position:ProductImage.objects.filter(pk=i.pk).update(position=position)
        if not ProductImage.objects.filter(product=product).exists() and product.online_visible:
            Product.objects.filter(pk=product.pk).update(online_visible=False)       # a live product always has a photo
    media.delete(path);return JsonResponse({'ok':True,'unpublished':not ProductImage.objects.filter(product=product).exists()})


def image_file(request,path):
    """Developer machines only: the live system serves photos from the public storage bucket."""
    from django.conf import settings
    if settings.SUPABASE_AUTH_ENABLED:raise Http404
    raw=media.read_local(path)
    if raw is None:raise Http404
    return HttpResponse(raw,content_type=media.sniff(raw) or 'application/octet-stream')


@login_required
def dashboard(request):
    """What the website is selling right now and what needs attention: live stock and value, per shop, margin, slow stock, and fix-it lists."""
    _owner(request)
    from datetime import date
    today=date.today();shops=list(Branch.objects.filter(enabled=True))
    live=list(Product.objects.filter(online_visible=True,enabled=True).select_related('model'));live_ids={p.pk for p in live}
    photos={r['product_id']:r['n'] for r in ProductImage.objects.values('product_id').annotate(n=Count('id'))}
    per_shop={b.pk:dict(shop=b.name,units=0,value=ZERO,cost=ZERO) for b in shops}
    by_product={p.pk:dict(p=p,units=0,value=ZERO,cost=ZERO,low=None,high=None,price_gap=False) for p in live}
    slow=[];below_cost=[]
    for d in Device.objects.filter(product_id__in=live_ids,status='Available',branch__in=shops).select_related('product','branch'):
        price=d.online_price if d.online_price is not None else d.product.online_price if d.product.online_price is not None else d.asking_price
        row=by_product[d.product_id];row['units']+=1;row['value']+=price;row['cost']+=d.purchase_cost
        row['low']=price if row['low'] is None else min(row['low'],price);row['high']=price if row['high'] is None else max(row['high'],price)
        if price!=d.asking_price:row['price_gap']=True
        s=per_shop[d.branch_id];s['units']+=1;s['value']+=price;s['cost']+=d.purchase_cost
        days=(today-d.received_on).days
        if days>=30:slow.append(dict(device=d,days=days,price=price))
        if price<=d.purchase_cost:below_cost.append(dict(device=d,price=price))
    for r in StockMovement.objects.filter(product_id__in=live_ids,product__serialized=False,warehouse__branch__in=shops).values('product_id','warehouse__branch_id').annotate(q=Sum('quantity'),v=Sum('value')):
        if (r['q'] or 0)<=0:continue
        p=by_product[r['product_id']]['p'];price=p.online_price if p.online_price is not None else p.selling_price
        row=by_product[r['product_id']];row['units']+=r['q'];row['value']+=price*r['q'];row['cost']+=r['v'] or ZERO;row['low']=row['high']=price
        if price!=p.selling_price:row['price_gap']=True
        s=per_shop[r['warehouse__branch_id']];s['units']+=r['q'];s['value']+=price*r['q'];s['cost']+=r['v'] or ZERO
    products=list(by_product.values())
    totals=dict(units=sum(r['units'] for r in products),value=sum((r['value'] for r in products),ZERO),cost=sum((r['cost'] for r in products),ZERO))
    totals['margin']=totals['value']-totals['cost']
    out_of_stock=[r for r in products if r['units']<=0]
    low_stock=[r for r in products if 0<r['units']<=2]
    gaps=[r for r in products if r['price_gap']]
    # stock in the shops that customers cannot see yet: the biggest opportunity to sell more online
    hidden=[]
    candidates=Device.objects.filter(status='Available',branch__in=shops).exclude(product_id__in=live_ids).values('product_id','product__name').annotate(n=Count('id'),lo=Min('asking_price')).order_by('-n')[:15]
    for r in candidates:hidden.append(dict(pk=r['product_id'],name=r['product__name'],units=r['n'],price=r['lo'],photos=photos.get(r['product_id'],0)))
    hidden_total=Device.objects.filter(status='Available',branch__in=shops).exclude(product_id__in=live_ids).count()
    return render(request,'online_dashboard.html',dict(title='Online Dashboard',live=len(live),totals=totals,shops=sorted(per_shop.values(),key=lambda s:-s['units']),
        top=sorted(products,key=lambda r:-r['value'])[:10],out_of_stock=out_of_stock,low_stock=low_stock,gaps=gaps,slow=sorted(slow,key=lambda r:-r['days'])[:10],below_cost=below_cost,
        hidden=hidden,hidden_total=hidden_total,no_photo=sum(1 for h in hidden if not h['photos'])))
