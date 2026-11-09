"""Typed phone details for the buy screens: brand, model, storage and colour are written (or picked) by the buyer and the
matching catalogue records are found or created on save. Matching ignores capitals and extra spaces, so "iphone 14 pro max"
and "iPhone 14 Pro Max " are the same phone."""
import uuid
from django.core.exceptions import ValidationError
from .models import Brand,PhoneModel,Product

STORAGE=['4GB','8GB','16GB','32GB','64GB','128GB','256GB','512GB','1TB','2TB']
COLOURS=['Black','White','Silver','Grey','Gold','Rose Gold','Blue','Green','Red','Purple','Pink','Yellow','Orange','Titanium','Graphite','Midnight','Starlight']


DEFAULT_BRANDS=['iPhone','Samsung','Xiaomi','Oppo','Vivo','Realme','Infinix','Tecno','Itel','Huawei','Honor','Google Pixel','OnePlus','Motorola','Nokia','Sony','Lenovo','Asus','Redmi','Poco']


def tidy(value):return ' '.join(str(value or '').split())


def catalog():
    """Brands, the models under each brand (lower-case brand key) and colours already in use, for the suggestion lists."""
    brands={b.casefold():b for b in DEFAULT_BRANDS}
    brands.update({b.casefold():b for b in Brand.objects.filter(enabled=True).values_list('name',flat=True)})
    models={}
    for name,brand in PhoneModel.objects.exclude(brand='').order_by('name').values_list('name','brand'):
        brands.setdefault(brand.casefold(),brand);models.setdefault(brand.casefold(),[])
        if name not in models[brand.casefold()]:models[brand.casefold()].append(name)
    used=set(Product.objects.filter(serialized=True).exclude(colour='').values_list('colour',flat=True))
    colours=sorted(set(COLOURS)|used,key=str.casefold)
    return dict(brands=sorted(brands.values(),key=str.casefold),models=models,colours=colours,storage=STORAGE)


def check(spec):
    """Validate typed details. Returns the cleaned dict or raises ValidationError with a plain message."""
    brand,model,storage,colour=(tidy(spec.get(k)) for k in ('brand','model','storage','colour'))
    if not brand:raise ValidationError('Enter the phone brand.')
    if not model:raise ValidationError('Enter the phone model.')
    if storage not in STORAGE:raise ValidationError('Choose the storage size.')
    if not colour:raise ValidationError('Enter the phone colour.')
    if len(brand)>80 or len(model)>150 or len(colour)>80:raise ValidationError('The brand, model or colour is too long.')
    return dict(brand=brand,model=model,storage=storage,colour=colour)


def resolve_variant(spec):
    """Find or create the brand, model and variant. Call inside a transaction (the buy screens already do)."""
    d=check(spec)
    existing=Brand.objects.filter(name__iexact=d['brand']).first()
    if not existing:
        known=PhoneModel.objects.filter(brand__iexact=d['brand']).values_list('brand',flat=True).first()
        existing=Brand.objects.create(name=known or d['brand'])
    brand=existing.name
    model=PhoneModel.objects.filter(brand__iexact=brand,name__iexact=d['model']).first()
    if not model:
        model=PhoneModel(code='MDL-'+uuid.uuid4().hex[:10].upper(),name=d['model'],brand=brand);model.full_clean();model.save()
    product=Product.objects.filter(model=model,storage__iexact=d['storage'],colour__iexact=d['colour'],serialized=True,stock_item=True,enabled=True).order_by('created').first()
    if not product:
        label=model.name if model.name.casefold().startswith(brand.casefold()) else f'{brand} {model.name}'
        product=Product(code='PHN-'+uuid.uuid4().hex[:10].upper(),name=f'{label} {d["storage"]} {d["colour"]}',group='Used Phones',model=model,brand=brand,storage=d['storage'],colour=d['colour'],serialized=True,stock_item=True)
        product.full_clean();product.save()
    return product
