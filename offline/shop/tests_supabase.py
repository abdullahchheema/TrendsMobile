import time,uuid
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase,SimpleTestCase,override_settings
from django.urls import reverse
from .models import *
from .tests import fixtures,imei
from .supabase_storage import SupabasePrivateStorage

@override_settings(SUPABASE_AUTH_ENABLED=True,AUTHENTICATION_BACKENDS=['shop.supabase_auth.SupabaseBackend'],
    SUPABASE_INTERNAL_URL='http://supabase.test',
    STORAGES={'default':{'BACKEND':'shop.supabase_storage.SupabasePrivateStorage'},'staticfiles':{'BACKEND':'whitenoise.storage.CompressedManifestStaticFilesStorage'}})
class SupabaseAuthentication(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key,value in fixtures().items():setattr(cls,key,value)
        cls.auth_id=uuid.uuid4();SupabaseIdentity.objects.create(auth_id=cls.auth_id,user=cls.user)
    def session(self,uid=None,access='private-access-token',refresh='private-refresh-token'):
        return dict(user={'id':str(uid or self.auth_id)},access_token=access,refresh_token=refresh,expires_at=int(time.time())+3600)
    def login(self):
        with patch('shop.supabase.sign_in',return_value=self.session()):
            response=self.client.post(reverse('login'),dict(username='owner@covison.local',password='not-written-to-django'))
        self.assertEqual(response.status_code,302)
    def test_provider_login_uses_fixed_identity_mapping(self):
        self.login()
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}):response=self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'private-access-token');self.assertNotContains(response,'private-refresh-token')
    def test_unlinked_provider_account_has_no_crm_access(self):
        with patch('shop.supabase.sign_in',return_value=self.session(uuid.uuid4())):
            response=self.client.post(reverse('login'),dict(username='unlinked@example.invalid',password='irrelevant'))
        self.assertEqual(response.status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
    def test_disabled_staff_cannot_sign_in(self):
        self.user.is_superuser=False;self.user.save();self.buyer.user=self.user;self.buyer.enabled=False;self.buyer.save()
        with patch('shop.supabase.sign_in',return_value=self.session()),patch('shop.supabase.sign_out'):
            response=self.client.post(reverse('login'),dict(username='staff@example.invalid',password='irrelevant'))
        self.assertEqual(response.status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
    def test_expired_access_token_refreshes_server_side(self):
        self.login();session=self.client.session;session['supabase']['expires_at']=0;session.save()
        with patch('shop.supabase.refresh',return_value=self.session(access='new-token')) as refresh,patch('shop.supabase.user',return_value={'id':str(self.auth_id)}):
            response=self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code,200);refresh.assert_called_once_with('private-refresh-token')
        self.assertEqual(self.client.session['supabase']['access_token'],'new-token')
    def test_revoked_identity_fails_closed(self):
        self.login()
        with patch('shop.supabase.user',side_effect=ValidationError('Revoked')):response=self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code,302);self.assertNotIn('_auth_user_id',self.client.session)
    def test_logout_revokes_the_provider_session(self):
        self.login()
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}),patch('shop.supabase.sign_out') as logout:
            response=self.client.post(reverse('logout'))
        self.assertEqual(response.status_code,302);logout.assert_called_once_with('private-access-token');self.assertNotIn('_auth_user_id',self.client.session)
    def test_password_change_routes_to_supabase(self):
        self.login()
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id),'email':'owner@covison.local'}),\
            patch('shop.supabase.sign_in',side_effect=[self.session(access='old-verification'),self.session(access='new-password-token')]),\
            patch('shop.supabase.sign_out'),patch('shop.supabase.call') as update:
            response=self.client.post(reverse('password'),dict(old_password='Old-secret',new_password1='Strong-New-Password-928!',new_password2='Strong-New-Password-928!'))
        self.assertEqual(response.status_code,302)
        update.assert_called_once_with('/auth/v1/user',method='PUT',payload={'password':'Strong-New-Password-928!'},token='private-access-token')
        self.assertEqual(self.client.session['supabase']['access_token'],'new-password-token')
    def test_other_shop_attachment_is_denied_before_storage_access(self):
        self.user.is_superuser=False;self.user.save();self.buyer.user=self.user;self.buyer.save()
        other=Branch.objects.create(code='OTHER',name='Other shop',company='Test Company')
        place=Warehouse.objects.create(code='OTHER-STORE',name='Other stores',branch=other)
        device=Device.objects.create(code='OTHER-PHONE',product=self.product,warehouse=place)
        IMEI.objects.create(number=imei(999),device=device,slot=1,evidence='pta/private.pdf')
        self.login()
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}),patch('shop.supabase.call') as download:
            response=self.client.get(reverse('attachment',args=['pta/private.pdf']))
        self.assertEqual(response.status_code,404);download.assert_not_called()
    def test_own_attachment_uses_server_side_private_storage(self):
        device=Device.objects.create(code='OWN-PHONE',product=self.product,warehouse=self.warehouse)
        IMEI.objects.create(number=imei(998),device=device,slot=1,evidence='pta/own.pdf')
        self.login()
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}),patch('shop.supabase.call',return_value=b'PDF content') as download:
            response=self.client.get(reverse('attachment',args=['pta/own.pdf']))
            self.assertEqual(b''.join(response.streaming_content),b'PDF content')
        self.assertEqual(response.status_code,200);download.assert_called_once_with('/storage/v1/object/pta-evidence/pta/own.pdf',admin=True,raw_response=True)

class SupabaseFileStorage(SimpleTestCase):
    def test_upload_uses_unique_private_object_and_app_url(self):
        storage=SupabasePrivateStorage()
        with patch('shop.supabase.call') as send:name=storage.save('pta/check.pdf',ContentFile(b'file content'))
        self.assertTrue(name.startswith('pta/'));self.assertTrue(name.endswith('check.pdf'))
        self.assertTrue(send.call_args.kwargs['admin']);self.assertEqual(send.call_args.kwargs['payload'],b'file content')
        self.assertTrue(storage.url(name).startswith('/attachments/'))
    def test_oversize_file_rejected_before_network_request(self):
        with patch('shop.supabase.call') as send:
            with self.assertRaises(ValidationError):SupabasePrivateStorage().save('pta/too-big.pdf',ContentFile(b'x'*10485761))
        send.assert_not_called()
