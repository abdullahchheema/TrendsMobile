"""One simple shop model: each shop has one stock location, one till and one "shop" person who stands behind everything done
in that shop. The records still exist underneath (so ledgers, stock and reports keep working), but the screens never ask
who bought, who sold, who took the money or which warehouse. Nothing here changes existing data."""
import uuid
from .models import Role,Staff,Till,Warehouse


def _unique(model,code):
    return code if not model.objects.filter(code=code).exists() else f'{code}-{uuid.uuid4().hex[:4].upper()}'


def warehouse(branch):
    """The shop's single stock location: its first enabled warehouse, created if the shop has none."""
    found=Warehouse.objects.filter(branch=branch,enabled=True).order_by('created').first()
    return found or Warehouse.objects.create(code=_unique(Warehouse,branch.code+'-STOCK'),name='Shop stock',branch=branch)


def till(branch):
    found=Till.objects.filter(branch=branch,enabled=True).order_by('created').first()
    return found or Till.objects.create(code=_unique(Till,branch.code+'-TILL'),name='Shop till',branch=branch)


def shop_person(branch):
    """The stand-in staff record for the shop itself: allowed to buy, sell and take payments."""
    code=branch.code+'-SHOP'
    person=Staff.objects.filter(branch=branch,code=code).first()
    if person:return person
    role=Role.objects.filter(code='manager').first()
    person=Staff(code=_unique(Staff,code),name=branch.name,branch=branch,designation='Shop',manager=True,can_buy=True,can_sell=True,can_handle_payments=True,role=role,default_warehouse=warehouse(branch))
    person.save();return person


def ensure(branch):
    """Make sure the shop has its stock location, till and shop person. Safe to call at any time."""
    return dict(warehouse=warehouse(branch),till=till(branch),person=shop_person(branch))


def person(user,branch):
    """Whoever is logged in for this shop. The shop's own login if it has one, otherwise the shop stand-in (owner working in a shop)."""
    if getattr(user,'is_authenticated',False):
        linked=Staff.objects.filter(user=user,branch=branch,enabled=True).first()
        if linked:return linked
    return shop_person(branch)
