from unittest.mock import patch
from django.test import TestCase,RequestFactory,override_settings
from django.db import DatabaseError
from django.http import HttpResponse
from django.urls import reverse
from .tests import fixtures
from .models import Document,StockMovement,LedgerEntry,Product
from .services import valid_imei
from .views import health
from .supabase_auth import SupabaseSessionMiddleware

class DeviceChecks(TestCase):
    def setUp(self):self.__dict__.update(fixtures())
    def test_device_tools_require_login(self):
        for name in ('device_check','device_print_test'):
            self.assertEqual(self.client.get(reverse(name)).status_code,302)
    def test_sample_print_formats_create_no_trading_records(self):
        self.client.force_login(self.user)
        for paper in ('58','80','a4'):
            response=self.client.get(reverse('device_print_test'),{'paper':paper})
            self.assertContains(response,'NOT A SALE');self.assertContains(response,'PKR 12,345.50')
            self.assertTrue(valid_imei(response.context['sample_imei']))
        self.assertFalse(Document.objects.exists());self.assertFalse(LedgerEntry.objects.exists());self.assertFalse(StockMovement.objects.exists())
        self.assertEqual(self.client.get(reverse('device_print_test'),{'paper':'unrecognized'}).status_code,404)
    def test_device_screen_explains_server_dependency_and_scanner_suffix(self):
        self.client.force_login(self.user);response=self.client.get(reverse('device_check'))
        self.assertContains(response,'Enter suffix');self.assertContains(response,'shared server')
        self.assertContains(response,'58 mm test');self.assertContains(response,'A4 test')
    def test_health_is_not_cached(self):
        response=self.client.get('/health/');self.assertEqual(response.status_code,200);self.assertEqual(response['Cache-Control'],'no-store')
    def test_pos_preserves_barcode_values_with_spaces_and_leading_zeroes(self):
        product=Product.objects.create(code='QA-SCANNER',name='QA scanner service',stock_item=False,barcodes=['QA UNIT 1','0000123'])
        self.client.force_login(self.user);response=self.client.get(reverse('pos'))
        row=next(item for item in response.context['items'] if item['product']==str(product.pk))
        self.assertEqual(row['barcodes'],['QA UNIT 1','0000123'])
    def test_database_failure_is_sanitized_for_connection_probe(self):
        with patch('django.db.connection.cursor',side_effect=DatabaseError('private database detail')):
            response=health(RequestFactory().get('/health/'))
        self.assertEqual(response.status_code,503);self.assertNotIn(b'private',response.content)
    @override_settings(SUPABASE_AUTH_ENABLED=True)
    def test_public_probe_does_not_refresh_or_revoke_staff_provider_session(self):
        request=RequestFactory().get('/health/')
        with patch('shop.supabase.user') as provider,patch('shop.supabase.refresh') as refresh:
            response=SupabaseSessionMiddleware(lambda r:HttpResponse('health'))(request)
        self.assertEqual(response.status_code,200);provider.assert_not_called();refresh.assert_not_called()
