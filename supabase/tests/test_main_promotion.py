import importlib,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
promotion=importlib.import_module('promote_main')


class PromotionSafeguards(unittest.TestCase):
    def test_env_update_keeps_unrelated_values_literal(self):
        original='DATABASE_PASSWORD=literal-$-value\nSITE_URL=http://old:8081\n'
        changed=promotion.update_env(original,{'SITE_URL':'http://192.168.1.2:8080','COVISON_MAIN_CRM_ENABLED':'1'})
        self.assertIn('DATABASE_PASSWORD=literal-$-value\n',changed)
        self.assertEqual(promotion.env_values(changed)['SITE_URL'],'http://192.168.1.2:8080')
        self.assertEqual(changed.count('SITE_URL='),1)
    def test_private_writes_are_atomic_and_owner_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings';promotion.write_secret(path,'placeholder-data')
            self.assertEqual(path.read_text(),'placeholder-data');self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(len(list(Path(folder).iterdir())),1)
    def test_new_business_activity_prevents_rollback_before_any_mutation(self):
        state={'business_before':{'shop_document':'old'}}
        with patch.object(promotion,'fingerprint',return_value={'shop_document':'new'}),patch.object(promotion,'write_secret') as write,patch.object(promotion,'control') as control,patch.object(promotion,'restore_services') as restore:
            with self.assertRaisesRegex(RuntimeError,'Business records have changed'):promotion.rollback(state)
            write.assert_not_called();control.assert_not_called();restore.assert_not_called()
    def test_unrelated_env_lines_remain_unchanged(self):
        original='# Header\nSECRET=placeholder=with=equals\nSITE_URL=http://old\n'
        changed=promotion.update_env(original,{'SITE_URL':'http://new'})
        self.assertEqual(promotion.env_values(changed)['SECRET'],'placeholder=with=equals');self.assertTrue(changed.startswith('# Header\n'))
