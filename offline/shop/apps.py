from django.apps import AppConfig


class ShopConfig(AppConfig):
    name='shop'
    verbose_name='Covison ERP'
    def ready(self):
        # A new colour/storage variant added to a page that already has photos goes live at once, with no publishing step.
        from django.db.models.signals import post_save
        from .models import Product
        post_save.connect(_new_variant,sender=Product,dispatch_uid='covison-live-pages')
        # Anything that changes what the website shows (stock, prices, photos, orders, reservations) pings it, so it updates at once.
        from django.db.models.signals import post_delete
        from .models import Device,OnlineAllocation,OnlineOrder,ProductImage,StockMovement
        from . import website_sync
        for model in (Device,Product,ProductImage,StockMovement,OnlineOrder,OnlineAllocation):
            post_save.connect(website_sync.notify,sender=model,dispatch_uid='covison-site-save-'+model.__name__)
            post_delete.connect(website_sync.notify,sender=model,dispatch_uid='covison-site-delete-'+model.__name__)


def _new_variant(sender,instance,created,raw=False,**kwargs):
    if raw or not created:return
    try:
        from .online_store import refresh_live
        refresh_live(instance)
    except Exception:       # never block saving a product because of the website
        pass
