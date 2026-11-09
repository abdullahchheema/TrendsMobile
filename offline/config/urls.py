from django.urls import path
from django.contrib.auth import views as auth
from shop import views,tax_views,easy_views,stock_adjust,search,control,coa
from shop.login_throttle import ThrottledLoginView
urlpatterns=[path('stock/adjust/',stock_adjust.adjust,name='stock_adjust'),path('search/',search.global_search,name='search'),path('settings/roles/',control.roles,name='roles'),path('settings/roles/new/',control.role_edit,name='role_new'),path('settings/roles/<slug:code>/',control.role_edit,name='role_edit'),path('audit/',control.audit_log,name='audit_log'),path('accounting/periods/',control.periods,name='periods'),path('accounting/chart/',coa.chart,name='chart'),path('sales-accounts/',easy_views.sales_accounts,name='sales_accounts'),path('entry/<str:kind>/',easy_views.entry,name='easy_entry'),path('entry/done/<uuid:pk>/',easy_views.done,name='easy_done'),path('',views.dashboard),path('health/',views.health),
    path('login/',ThrottledLoginView.as_view(),name='login'),path('logout/',views.sign_out,name='logout'),
    path('account/password/',views.change_password,name='password'),
    path('accounts/',views.users,name='users'),
    path('setup/',views.setup_dashboard,name='setup'),path('setup/new/',views.setup_new,name='setup_new'),
    path('setup/tax/',tax_views.listing,name='tax_rules'),path('setup/tax/new/',tax_views.edit,name='tax_rule_new'),path('setup/tax/<uuid:pk>/',tax_views.edit,name='tax_rule_edit'),
    path('device-check/',views.device_check,name='device_check'),path('device-check/print/',views.device_print_test,name='device_print_test'),
    path('setup/<uuid:pk>/finance/',views.setup_finance,name='setup_finance'),
    path('desk/mobile-shop',views.dashboard,name='dashboard'),path('desk/point-of-sale',views.pos,name='pos'),
    path('reports/',views.report_center,name='report_center'),
    path('api/pos/checkout',views.pos_checkout,name='pos_checkout'),
    path('journals/',views.journal_list,name='journals'),path('journals/new/',views.journal_edit,name='journal_new'),path('journals/<uuid:pk>/edit/',views.journal_edit,name='journal_edit'),
    path('fund-transfers/',views.fund_transfer_list,name='fund_transfers'),path('fund-transfers/new/',views.fund_transfer_edit,name='fund_transfer_new'),
    path('fund-transfers/<uuid:pk>/edit/',views.fund_transfer_edit,name='fund_transfer_edit'),
    path('records/<str:kind>/',views.collection,name='collection'),
    path('master/<str:kind>/new/',views.master_edit,name='master_new'),path('master/<str:kind>/<uuid:pk>/',views.master_edit,name='master_edit'),
    path('device/<uuid:pk>/',views.device_edit,name='device'),
    path('documents/<str:kind>/new/',views.create_document,name='new'),path('documents/<str:kind>/<uuid:pk>/edit/',views.create_document,name='edit'),
    path('document/<uuid:pk>/',views.detail,name='detail'),path('document/<uuid:pk>/<str:name>/',views.action,name='action'),
    path('receipt/<uuid:pk>/',views.receipt,name='receipt'),path('evidence/<str:pk>/',views.evidence,name='evidence'),
    path('attachments/<path:name>',views.attachment,name='attachment'),
    path('desk/query-report/<str:name>',views.report,name='report'),path('settings/',views.shop_settings,name='settings'),
    path('desk/<str:slug>/<str:code>',views.native_route),path('desk/<str:slug>',views.native_route)]
