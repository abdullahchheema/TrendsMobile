from django.apps import AppConfig


class ShopConfig(AppConfig):
    name='shop'
    verbose_name='Covison ERP'
    def ready(self):
        # A new colour/storage variant added to a page that already has photos goes live at once, with no publishing step.
        from django.db.models.signals import post_save
        from .models import Product
        post_save.connect(_new_variant,sender=Product,dispatch_uid='covison-live-pages')


def _new_variant(sender,instance,created,raw=False,**kwargs):
    if raw or not created:return
    try:
        from .online_store import refresh_live
        refresh_live(instance)
    except Exception:       # never block saving a product because of the website
        pass
