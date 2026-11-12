"""Hosted-deployment settings: DATABASE_URL parsing, Vercel hostnames, cache and static selection."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase
from config import deploy


class DatabaseSettings(SimpleTestCase):
    URL='postgresql://covison_app.abcdefgh:p%40ss@aws-0-ap-south-1.pooler.supabase.com:6543/postgres'
    def test_pooled_url_disables_persistent_connections_cursors_and_prepared_statements(self):
        db=deploy.database({'DATABASE_URL':self.URL})
        self.assertEqual((db['USER'],db['PASSWORD'],db['HOST'],db['PORT'],db['NAME']),('covison_app.abcdefgh','p@ss','aws-0-ap-south-1.pooler.supabase.com','6543','postgres'))
        self.assertEqual(db['CONN_MAX_AGE'],300);self.assertTrue(db['CONN_HEALTH_CHECKS']);self.assertTrue(db['DISABLE_SERVER_SIDE_CURSORS'])
        self.assertIsNone(db['OPTIONS']['prepare_threshold']);self.assertEqual(db['OPTIONS']['sslmode'],'require');self.assertNotIn('options',db['OPTIONS'])
    def test_other_pooler_ports_open_one_connection_per_request_unless_asked(self):
        session=self.URL.replace(':6543/',':5432/')
        self.assertEqual(deploy.database({'DATABASE_URL':session})['CONN_MAX_AGE'],0)
        self.assertEqual(deploy.database({'DATABASE_URL':session,'DATABASE_CONN_MAX_AGE':'45'})['CONN_MAX_AGE'],45)
    def test_malformed_url_is_rejected_clearly(self):
        for bad in ('mysql://u:p@h/db','postgresql://hostonly','not a url'):
            with self.assertRaisesMessage(ValueError,'DATABASE_URL must look like'):deploy.database({'DATABASE_URL':bad})
    def test_self_hosted_variables_still_work_and_keep_the_schema_option(self):
        db=deploy.database({'DATABASE_PASSWORD':'x','DATABASE_SCHEMA':'covison_app'})
        self.assertEqual(db['HOST'],'db');self.assertIn('search_path=covison_app',db['OPTIONS']['options']);self.assertEqual(db['CONN_MAX_AGE'],60)


class HostsAndModes(SimpleTestCase):
    def test_vercel_hostnames_are_trusted_automatically_without_duplicates(self):
        hosts,origins=deploy.hosts_and_origins({'DJANGO_ALLOWED_HOSTS':'erp.example.com','VERCEL_URL':'app-abc.vercel.app','VERCEL_PROJECT_PRODUCTION_URL':'app-abc.vercel.app','COVISON_TRUSTED_ORIGINS':'https://erp.example.com'})
        self.assertEqual(hosts,['erp.example.com','app-abc.vercel.app']);self.assertEqual(origins,['https://erp.example.com','https://app-abc.vercel.app'])
    def test_defaults_stay_local_only(self):
        self.assertEqual(deploy.hosts_and_origins({}),(['localhost','127.0.0.1'],[]))
    def test_cache_and_static_modes(self):
        self.assertTrue(deploy.uses_database_cache({'DATABASE_URL':'x'}));self.assertFalse(deploy.uses_database_cache({}))
        self.assertTrue(deploy.static_from_finders({'VERCEL':'1'}));self.assertFalse(deploy.static_from_finders({}))


class VercelPathDecoding(SimpleTestCase):
    def test_percent_encoded_path_is_decoded_only_on_vercel(self):
        import os
        from unittest.mock import patch
        from config.wsgi import decode_path_info
        with patch.dict(os.environ,{'VERCEL':'1'}):
            self.assertEqual(decode_path_info({'PATH_INFO':'/records/Purchase%20Receipt/'})['PATH_INFO'],'/records/Purchase Receipt/')
        with patch.dict(os.environ,{},clear=False):
            os.environ.pop('VERCEL',None)
            self.assertEqual(decode_path_info({'PATH_INFO':'/records/Purchase%20Receipt/'})['PATH_INFO'],'/records/Purchase%20Receipt/')



class VercelSpacedUrlEndToEnd(TestCase):
    def call(self,vercel):
        import os
        from unittest.mock import patch
        from wsgiref.util import setup_testing_defaults
        from config.wsgi import application
        user=get_user_model().objects.create_superuser('wsgi-owner',password='Unrelated-test-password-729!');self.client.force_login(user)
        environ={'REQUEST_METHOD':'GET'};setup_testing_defaults(environ);environ['PATH_INFO']='/records/Purchase%20Receipt/';environ['HTTP_COOKIE']='sessionid='+self.client.cookies['sessionid'].value
        seen=[]
        # Calling the WSGI application directly fires Django's request signals, which close a database connection older than CONN_MAX_AGE:
        # the very connection this test's transaction lives on. The test client switches that off for the same reason; so does this.
        from django.core import signals
        from django.db import close_old_connections
        signals.request_started.disconnect(close_old_connections);signals.request_finished.disconnect(close_old_connections)
        try:
            with patch.dict(os.environ,{'VERCEL':'1'} if vercel else {}):
                if not vercel:os.environ.pop('VERCEL',None)
                list(application(environ,lambda status,headers,exc=None:seen.append(status)))
        finally:
            signals.request_started.connect(close_old_connections);signals.request_finished.connect(close_old_connections)
        return seen[0]
    def test_runtime_passing_an_encoded_path_now_reaches_the_page(self):
        self.assertTrue(self.call(True).startswith('200'))
    def test_without_decoding_the_same_request_is_a_404(self):
        self.assertTrue(self.call(False).startswith('404'))
