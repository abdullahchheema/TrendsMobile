import unittest
from supabase.main_route import enabled


class MainRoute(unittest.TestCase):
    def test_disabled_installations_keep_the_existing_route(self):
        self.assertFalse(enabled({}))
    def test_explicit_private_lan_route_is_valid(self):
        self.assertTrue(enabled({'COVISON_MAIN_CRM_ENABLED':'1','COVISON_BIND_ADDRESS':'192.168.10.2'}))
    def test_invalid_bindings_cannot_publish_main_route(self):
        for address in ('0.0.0.0','127.0.0.1','8.8.8.8','::1','192.168.1.0/24',''):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):enabled({'COVISON_MAIN_CRM_ENABLED':'1','COVISON_BIND_ADDRESS':address})
    def test_conflicting_or_invalid_ports_rejected(self):
        for port in ('8081','8765','0','65536','not-a-port'):
            with self.assertRaises(ValueError):enabled({'COVISON_MAIN_CRM_ENABLED':'1','COVISON_BIND_ADDRESS':'192.168.10.2','COVISON_MAIN_PORT':port})
    def test_ambiguous_enable_flags_rejected(self):
        with self.assertRaises(ValueError):enabled({'COVISON_MAIN_CRM_ENABLED':'yes'})
