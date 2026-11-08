"""Hosted-deployment settings: DATABASE_URL parsing, Vercel hostnames, cache and static selection."""
from django.test import SimpleTestCase
from config import deploy


class DatabaseSettings(SimpleTestCase):
    URL='postgresql://covison_app.abcdefgh:p%40ss@aws-0-ap-south-1.pooler.supabase.com:6543/postgres'
    def test_pooled_url_disables_persistent_connections_cursors_and_prepared_statements(self):
        db=deploy.database({'DATABASE_URL':self.URL})
        self.assertEqual((db['USER'],db['PASSWORD'],db['HOST'],db['PORT'],db['NAME']),('covison_app.abcdefgh','p@ss','aws-0-ap-south-1.pooler.supabase.com','6543','postgres'))
        self.assertEqual(db['CONN_MAX_AGE'],0);self.assertTrue(db['DISABLE_SERVER_SIDE_CURSORS'])
        self.assertIsNone(db['OPTIONS']['prepare_threshold']);self.assertEqual(db['OPTIONS']['sslmode'],'require');self.assertNotIn('options',db['OPTIONS'])
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
