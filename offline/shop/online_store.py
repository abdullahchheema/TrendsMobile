"""Online catalogue (the owner and the online-store login): every product of every shop, one page per phone model, with its variants.

* A website page is one phone model (or one accessory) with its variants (storage, colour, condition) and the stock of every shop.
* Photos are added here, for the page (shared by all variants) or for one variant. As soon as a page has a photo it is LIVE on the website;
  until then it only exists here as a draft. The owner can also hide a page.
* Prices: the shop price (set when stock is bought, used by the POS) is never changed here. Online price, percentage off and the crossed-out
  "compare" price are set here per variant.
The website reads the read-only views (migration 0026)."""
import json
from decimal import ROUND_HALF_UP,Decimal,InvalidOperation
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
from . import product_media as media,online_access,online_orders as oo,specs as specmod

ZERO=Decimal('0.00')
MAX_SHARED=12
MAX_PER_VARIANT=6


def _owner(request):
    if not online_access.can_use_online(request.user):raise PermissionDenied('Only the owner and the online-store login manage the online store.')


def title_of(product):
    """The website page a product belongs to: its own page title, else the model (phones), else its name. Variants share a page."""
    if product.web_title.strip():return product.web_title.strip()
    if product.model_id:return f'{product.model.brand} {product.model.name}'.strip()
    return product.name


def brand_of(product):
    return (product.model.brand if product.model_id else product.brand) or 'Other'


def family_of(product):
    """Every product on the same page (the product itself included)."""
    if product.web_title.strip():return Product.objects.filter(web_title__iexact=product.web_title.strip())
    if product.model_id:return Product.objects.filter(model_id=product.model_id,web_title='')
    return Product.objects.filter(pk=product.pk)


def make_slug(product):
    base=slugify(product.name)[:150] or 'product';slug=base;n=2
    while Product.objects.filter(slug=slug).exclude(pk=product.pk).exists():slug=f'{base}-{n}';n+=1
    return slug


def readiness(family):
    """What a page still needs before it can go live: a photo, and (phones) the required specifications."""
    has_photo=ProductImage.objects.filter(product__in=[p.pk for p in family]).exists()
    phone=any(p.serialized for p in family)
    source=next((p.specs for p in family if p.specs),{})
    missing=specmod.missing_required(source) if phone else []
    return dict(photo=has_photo,missing=missing,phone=phone,ok=has_photo and not missing,hidden=any(p.online_hidden for p in family))


def refresh_live(product):
    """A page is live exactly when it has a photo, (for phones) the required specifications, and is not hidden. Called whenever photos or the page
    change, and when a new variant is added to a page, so nothing needs publishing by hand. A new variant inherits the page's text and specifications."""
    product=Product.objects.select_related('model').get(pk=product.pk)
    family=list(family_of(product).select_related('model'))
    ready=readiness(family)['ok']
    page=slugify(title_of(product))[:150] or (product.slug or 'product')
    source=next((p for p in family if p.specs),None);text=next((p.description for p in family if p.description),'')
    for p in family:
        want=bool(ready and p.enabled and not p.online_hidden);changes={}
        if source is not None and not p.specs:changes['specs']=source.specs
        if text and not p.description:changes['description']=text
        if want and not p.slug:changes['slug']=make_slug(p)
        if p.family_slug!=page:changes['family_slug']=page
        if p.online_visible!=want:changes['online_visible']=want
        if changes:Product.objects.filter(pk=p.pk).update(**changes)


def sync_all():
    """Safety net for the catalogue screens: bring every page's live/draft state in line with its photos."""
    seen=set()
    for p in Product.objects.filter(enabled=True).filter(Q(serialized=True)|Q(stock_item=True)).select_related('model'):
        if p.pk in seen or p.code==oo.DELIVERY_CODE:continue
        fam=list(family_of(p).values_list('pk',flat=True));seen.update(fam);refresh_live(p)


def catalog(q=''):
    """Brand -> models -> variants, with stock per shop. Pages that never had stock, photos or a live flag are left out as noise."""
    products=list(Product.objects.filter(enabled=True).filter(Q(serialized=True)|Q(stock_item=True)).exclude(code=oo.DELIVERY_CODE).select_related('model'))
    by_product={p.pk:[] for p in products}
    for v in oo.variants(published_only=False):by_product[v['product'].pk].append(v)
    images=ProductImage.objects.filter(product_id__in=list(by_product))
    img_by_product={}
    for i in images:img_by_product.setdefault(i.product_id,[]).append(i)
    names={b.pk:b.name for b in oo.shops()}
    families={}
    for p in products:
        key=title_of(p).casefold();fam=families.setdefault(key,dict(title=title_of(p),brand=brand_of(p),products=[],rows=[],photos=0,cover='',live=False,hidden=False))
        fam['products'].append(p);fam['rows']+=by_product[p.pk]
        mine=sorted(img_by_product.get(p.pk,[]),key=lambda i:(i.variant_only,i.position))
        fam['photos']+=len(mine);fam['live']=fam['live'] or p.online_visible;fam['hidden']=fam['hidden'] or p.online_hidden
        if mine and not fam['cover']:fam['cover']=mine[0].url
    rows=[]
    for fam in families.values():
        fam['rows'].sort(key=lambda r:(r['product'].storage,r['product'].colour,r['condition'],r['price']))
        fam['stock']=sum(r['quantity'] for r in fam['rows'])
        for r in fam['rows']:r['shops']=sorted((names.get(b,'?'),n) for b,n in r['per_shop'].items())
        if not fam['stock'] and not fam['photos'] and not fam['live']:continue
        per={}
        for r in fam['rows']:
            for b,n in r['per_shop'].items():per[names.get(b,'?')]=per.get(names.get(b,'?'),0)+n
        fam['per_shop']=sorted(per.items());prices=[r['price'] for r in fam['rows']]
        fam['low']=min(prices) if prices else None;fam['high']=max(prices) if prices else None
        fam['anchor']=sorted(fam['products'],key=lambda p:(p.storage,p.colour,p.name))[0]
        fam['status']='Hidden' if fam['hidden'] else 'Live' if fam['live'] else 'Draft'
        ready=readiness(fam['products']);fam['reasons']=([] if ready['photo'] else ['no photo'])+(['specifications missing'] if ready['missing'] else [])
        fam['sale']=any((r['compare'] or 0)>r['price'] for r in fam['rows'])
        if q and q.lower() not in (fam['title']+' '+fam['brand']+' '+' '.join(p.name for p in fam['products'])).lower():continue
        rows.append(fam)
    rows.sort(key=lambda f:(f['brand'].casefold(),f['title'].casefold()))
    return rows


@login_required
def store(request):
    _owner(request);sync_all()
    q=request.GET.get('q','').strip();flt=request.GET.get('show','')
    rows=catalog(q)
    counts={n:sum(1 for r in rows if r['status']==n) for n in ('Live','Draft','Hidden')}
    if flt in counts:rows=[r for r in rows if r['status']==flt]
    brands=[];
    for r in rows:
        if not brands or brands[-1]['brand']!=r['brand']:brands.append(dict(brand=r['brand'],models=[]))
        brands[-1]['models'].append(r)
    return render(request,'online_store.html',dict(title='Products & Photos',brands=brands,q=q,show=flt,counts=counts,total=len(rows)))


def _payload(images):
    return [dict(id=str(i.pk),url=i.url,alt=i.alt) for i in images]


def _money(text,label,allow_empty=True):
    text=(text or '').strip().replace(',','')
    if not text:
        if allow_empty:return None
        raise ValidationError(f'Enter {label}.')
    try:value=Decimal(text)
    except InvalidOperation:raise ValidationError(f'{label} must be a number.')
    return value


@login_required
def edit(request,pk):
    _owner(request);anchor=get_object_or_404(Product.objects.select_related('model'),pk=pk)
    family=list(family_of(anchor).select_related('model').order_by('storage','colour','name'))
    rows_by_product={p.pk:[] for p in family}
    names={b.pk:b.name for b in oo.shops()}
    for v in oo.variants(published_only=False):
        if v['product'].pk in rows_by_product:rows_by_product[v['product'].pk].append(v)
    if request.method=='POST':
        try:
            with transaction.atomic():
                locked={p.pk:p for p in Product.objects.select_for_update().select_related('model').filter(pk__in=[p.pk for p in family])}
                title=' '.join(request.POST.get('web_title','').split())[:200];desc=request.POST.get('description','').strip()[:5000];hidden=request.POST.get('online_hidden')=='on'
                page_specs=specmod.parse(request.POST) if any(k.startswith(('spec_','other_label_')) for k in request.POST) else None      # a post without any specification field leaves them as they are
                all_pct=_money(request.POST.get('discount_all'),'the percentage off')
                for p in family:
                    p=locked[p.pk]
                    price=_money(request.POST.get(f'online_price_{p.pk}'),'the online price')
                    pct=all_pct if all_pct is not None else _money(request.POST.get(f'discount_{p.pk}'),'the percentage off')
                    compare=_money(request.POST.get(f'compare_{p.pk}'),'the compare price')
                    if price is not None and price<=0:raise ValidationError(f'{p.name}: the online price must be more than zero, or empty to use the shop price.')
                    if pct is not None and not (0<=pct<=90):raise ValidationError(f'{p.name}: the percentage off must be between 0 and 90.')
                    if compare is not None and compare<=0:raise ValidationError(f'{p.name}: the compare price must be more than zero, or empty.')
                    p.online_price=price;p.discount_percent=(pct if pct else None);p.compare_price=compare
                    p.description=desc;p.online_hidden=hidden;p.web_title=title
                    if page_specs is not None:p.specs=page_specs
                    p.save(update_fields=['online_price','discount_percent','compare_price','description','online_hidden','web_title','specs','modified'])
                for p in family:
                    fresh=Product.objects.select_related('model').get(pk=p.pk)
                    for v in oo.variants(published_only=False):
                        if v['product'].pk==fresh.pk and v['compare'] is not None and v['compare']<=v['price']:
                            raise ValidationError(f'{fresh.name}: the compare price ({v["compare"]:,.2f}) must be higher than the selling price ({v["price"]:,.2f}).')
                refresh_live(anchor)
            messages.success(request,'Saved.');return redirect('online_edit',pk=pk)
        except ValidationError as error:
            from .views import error_text
            messages.error(request,error_text(error))
            family=list(family_of(anchor).select_related('model').order_by('storage','colour','name'))
    anchor=Product.objects.select_related('model').get(pk=pk)
    ids=[p.pk for p in family]
    shared=list(ProductImage.objects.filter(product_id__in=ids,variant_only=False).order_by('position','created'))
    own={}
    for i in ProductImage.objects.filter(product_id__in=ids,variant_only=True).order_by('position','created'):own.setdefault(i.product_id,[]).append(i)
    variants=[dict(p=p,rows=[dict(r,shops=sorted((names.get(b,'?'),n) for b,n in r['per_shop'].items())) for r in rows_by_product[p.pk]],photos=own.get(p.pk,[])) for p in family]
    shop_list=oo.shops()
    matrix=[]                                  # where the stock is: one line per variant, one column per shop
    for v in variants:
        for r in v['rows']:matrix.append(dict(label=(f"{v['p'].storage} {v['p'].colour}".strip() or v['p'].name),detail=(f"{r['condition']} · {r['pta_status']}" if r['condition'] else ''),price=r['price'],compare=r['compare'],total=r['quantity'],cells=[r['per_shop'].get(b.pk,0) for b in shop_list]))
    shop_totals=[sum(m['cells'][i] for m in matrix) for i in range(len(shop_list))]
    live=any(p.online_visible for p in family)
    spec_groups,spec_custom=specmod.form_groups(next((p.specs for p in family if p.specs),{}))
    need=readiness(family)
    return render(request,'online_edit.html',dict(title=title_of(anchor),page_title=title_of(anchor),anchor=anchor,product=anchor,variants=variants,images=json.dumps(_payload(shared)),
        status='Hidden' if anchor.online_hidden else 'Live' if live else 'Draft',live=live,max_photos=MAX_SHARED,max_mb=media.MAX_BYTES//1048576,
        stock=sum(r['quantity'] for rows in rows_by_product.values() for r in rows),spec_groups=spec_groups,spec_custom=spec_custom,need=need,required_labels=[specmod.LABEL[k] for k in specmod.REQUIRED],matrix=matrix,shop_list=shop_list,shop_totals=shop_totals))


def _family_ids(product):return list(family_of(Product.objects.select_related('model').get(pk=product.pk)).values_list('pk',flat=True))


@login_required
@require_POST
def photo_upload(request,pk):
    _owner(request);product=get_object_or_404(Product,pk=pk)
    try:
        upload=request.FILES.get('file')
        if not upload:raise ValidationError('Choose a photo.')
        raw=upload.read(media.MAX_BYTES+1);ids=_family_ids(product);variant=None
        if request.POST.get('variant'):
            variant=get_object_or_404(Product,pk=request.POST['variant'])
            if variant.pk not in ids:raise ValidationError('That variant is not on this page.')
        with transaction.atomic():
            list(Product.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
            scope=ProductImage.objects.filter(product=variant,variant_only=True) if variant else ProductImage.objects.filter(product_id__in=ids,variant_only=False)
            count=scope.count();limit=MAX_PER_VARIANT if variant else MAX_SHARED
            if count>=limit:raise ValidationError(f'At most {limit} photos {"for this variant" if variant else "on a page"}.')
            owner=variant or product
            path,url,size=media.save(owner.pk,raw)
            nxt=(scope.aggregate(m=Max('position'))['m'] or 0)+1 if count else 0
            image=ProductImage.objects.create(product=owner,path=path,url=url,size=size,position=nxt,alt=title_of(product)[:200],variant_only=bool(variant))
            refresh_live(product)
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
        mine={str(i.pk):i for i in ProductImage.objects.select_for_update().filter(product_id__in=_family_ids(product),variant_only=False)}
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
        list(Product.objects.select_for_update().filter(pk__in=_family_ids(product)).order_by('pk'))
        path=image.path;shared=not image.variant_only;image.delete()
        if shared:
            for position,i in enumerate(ProductImage.objects.filter(product_id__in=_family_ids(product),variant_only=False).order_by('position','created')):
                if i.position!=position:ProductImage.objects.filter(pk=i.pk).update(position=position)
        refresh_live(product)                                         # no photo left = back to a draft
    media.delete(path)
    live=Product.objects.filter(pk=product.pk,online_visible=True).exists()
    return JsonResponse({'ok':True,'unpublished':not live})


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
    _owner(request);sync_all()
    from datetime import date
    today=date.today();shops=oo.shops()
    live=list(Product.objects.filter(online_visible=True,enabled=True).select_related('model'));live_ids={p.pk for p in live}
    per_shop={b.pk:dict(shop=b.name,units=0,value=ZERO,cost=ZERO) for b in shops}
    by_product={p.pk:dict(p=p,units=0,value=ZERO,cost=ZERO,low=None,high=None,price_gap=False) for p in live}
    slow=[];below_cost=[]
    for d in Device.objects.filter(product_id__in=live_ids,status='Available',branch__in=shops).select_related('product','branch'):
        price=oo.device_price(d,d.product)
        row=by_product[d.product_id];row['units']+=1;row['value']+=price;row['cost']+=d.purchase_cost
        row['low']=price if row['low'] is None else min(row['low'],price);row['high']=price if row['high'] is None else max(row['high'],price)
        if price!=d.asking_price:row['price_gap']=True
        s=per_shop[d.branch_id];s['units']+=1;s['value']+=price;s['cost']+=d.purchase_cost
        days=(today-d.received_on).days
        if days>=30:slow.append(dict(device=d,days=days,price=price))
        if price<=d.purchase_cost:below_cost.append(dict(device=d,price=price))
    for r in StockMovement.objects.filter(product_id__in=live_ids,product__serialized=False,warehouse__branch__in=shops).values('product_id','warehouse__branch_id').annotate(q=Sum('quantity'),v=Sum('value')):
        if (r['q'] or 0)<=0:continue
        p=by_product[r['product_id']]['p'];price=oo.product_price(p)
        row=by_product[r['product_id']];row['units']+=r['q'];row['value']+=price*r['q'];row['cost']+=r['v'] or ZERO;row['low']=row['high']=price
        if price!=p.selling_price:row['price_gap']=True
        s=per_shop[r['warehouse__branch_id']];s['units']+=r['q'];s['value']+=price*r['q'];s['cost']+=r['v'] or ZERO
    products=list(by_product.values())
    totals=dict(units=sum(r['units'] for r in products),value=sum((r['value'] for r in products),ZERO),cost=sum((r['cost'] for r in products),ZERO))
    totals['margin']=totals['value']-totals['cost']
    out_of_stock=[r for r in products if r['units']<=0]
    low_stock=[r for r in products if 0<r['units']<=2]
    gaps=[r for r in products if r['price_gap']]
    # stock in the shops that is not live yet (no photo): the biggest opportunity to sell more online
    hidden=[]
    candidates=Device.objects.filter(status='Available',branch__in=shops).exclude(product_id__in=live_ids).values('product_id','product__name').annotate(n=Count('id'),lo=Min('asking_price')).order_by('-n')[:15]
    for r in candidates:hidden.append(dict(pk=r['product_id'],name=r['product__name'],units=r['n'],price=r['lo'],photos=0))
    hidden_total=Device.objects.filter(status='Available',branch__in=shops).exclude(product_id__in=live_ids).count()
    from . import online_accounts
    month=today.replace(day=1);acct=online_accounts.summary(month,today)
    see_cost=request.user.is_superuser
    if not see_cost:totals=dict(totals,cost=None,margin=None);below_cost=[]
    return render(request,'online_dashboard.html',dict(title='Online Dashboard',acct=acct,see_cost=see_cost,live=len(live),totals=totals,shops=sorted(per_shop.values(),key=lambda s:-s['units']),
        top=sorted(products,key=lambda r:-r['value'])[:10],out_of_stock=out_of_stock,low_stock=low_stock,gaps=gaps,slow=sorted(slow,key=lambda r:-r['days'])[:10],below_cost=below_cost,
        hidden=hidden,hidden_total=hidden_total,no_photo=len(hidden)))
