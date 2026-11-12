"""The online-store login: a person who runs the website's orders, online stock, products and photos, and online refunds, and nothing else.

How it stays safe
  * The login has no shop. Outside the online screens every request is refused (and the dashboard link sends them to the Online Dashboard).
  * Inside the online screens (a fixed list of pages) the login may act on any shop's stock and on online records, because an online order can
    be supplied by any shop. Those pages only ever touch online orders and the invoices, returns and payments that belong to them: the
    middleware checks that for every page that opens a single record.
  * Shop money screens, other shops' customers, reports, settings and the POS are never reachable."""
from contextvars import ContextVar
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import Resolver404,resolve,reverse

_active=ContextVar('covison_online_mode',default=False)

PAGES={'online_settings','live_state','online_dashboard','online_store','online_edit','online_photo_upload','online_photo_order','online_photo_update','online_photo_delete','product_image_file','online_orders','online_order',
       'online_order_new','online_stock','online_accounts','online_money_in','online_money_out','online_expenses','online_customer_dues','online_cash_and_bank','online_history','logout','password','display_name','login'}
PARTY_PAGES={'party_account'}                                          # a customer's account: only Online Store customers
RECORD_PAGES={'return_sale','return_done','detail','receipt'}        # open one invoice, return or receipt: only if it belongs to an online order


def active():return _active.get()


def is_online(user):
    """True for an enabled online-store login (never for the owner or a shop login)."""
    if not getattr(user,'is_authenticated',False) or user.is_superuser:return False
    cached=getattr(user,'_covison_is_online',None)
    if cached is None:
        from .models import OnlineStaff
        cached=user._covison_is_online=OnlineStaff.objects.filter(user=user,enabled=True).exists()
    return cached


def can_use_online(user):
    return bool(getattr(user,'is_authenticated',False)) and (user.is_superuser or is_online(user))


class OnlineAccessMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        user=getattr(request,'user',None)
        if not getattr(user,'is_authenticated',False) or user.is_superuser or not is_online(user):return self.get_response(request)
        try:match=resolve(request.path_info)
        except Resolver404:return self.get_response(request)
        name=match.url_name
        if name=='dashboard' or request.path_info=='/':return redirect('online_dashboard')
        if name in RECORD_PAGES:
            from .models import Document
            if not Document.objects.filter(pk=match.kwargs.get('pk'),channel='Online').exists():raise PermissionDenied('This record is not part of the online store.')
        elif name in PARTY_PAGES:
            from .models import Party
            if not Party.objects.filter(pk=match.kwargs.get('pk'),branch__is_online=True).exists():raise PermissionDenied('This record is not part of the online store.')
        elif name not in PAGES:raise PermissionDenied('Your login only runs the online store.')
        token=_active.set(True)
        try:return self.get_response(request)
        finally:_active.reset(token)


def menu(path=''):
    r=reverse
    rows=[dict(title='Online Dashboard',icon='home',url=r('online_dashboard'),items=[],open=False),
            dict(title='Orders',icon='buy',url=r('online_orders'),items=[dict(label='All Orders',url=r('online_orders'),current=False),dict(label='New Orders',url=r('online_orders')+'?status=New',current=False),dict(label='Dispatched',url=r('online_orders')+'?status=Dispatched',current=False),dict(label='New Order (phone / WhatsApp)',url=r('online_order_new'),current=False)],open=False),
            dict(title='Online Stock',icon='stock',url=r('online_stock'),items=[],open=False),
            dict(title='Delivery & Payments',icon='settings',url=r('online_settings'),items=[],open=False),
            dict(title='Online Finance',icon='payments-expenses',url=r('online_money_in'),items=[dict(label='Money In',url=r('online_money_in'),current=False),dict(label='Money Out',url=r('online_money_out'),current=False),dict(label='Online Expenses',url=r('online_expenses'),current=False),dict(label='Customer Dues (COD)',url=r('online_customer_dues'),current=False),dict(label='Cash and Bank',url=r('online_cash_and_bank'),current=False),dict(label='Transaction History',url=r('online_history'),current=False),dict(label='Online Accounts & Profit',url=r('online_accounts'),current=False)],open=False),
            dict(title='Products & Photos',icon='phones',url=r('online_store'),items=[],open=False),
            dict(title='My Account',icon='settings',url=r('password'),items=[dict(label='Name and password',url=r('password'),current=False)],open=False)]
    here=path.split('?')[0]
    for s in rows:
        for item in s['items']:item['current']=path==item['url']
        s['open']=here==s['url'].split('?')[0] or any(i['current'] for i in s['items']) or (s['title']=='Orders' and here.startswith('/online/orders')) or (s['title']=='Online Finance' and here.startswith('/online/finance'))
    return rows
