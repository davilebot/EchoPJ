import tempfile
import unittest
from pathlib import Path

from service.saas import SaaSError, SaaSStore


COMPANY_A = {"cnpj": "11222333000181", "legal_name": "Empresa A", "uf": "SP"}
COMPANY_B = {"cnpj": "19131243000197", "legal_name": "Empresa B", "uf": "RJ"}


class SaaSStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SaaSStore(str(Path(self.tmp.name) / "saas.sqlite"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_internal_organization_has_unlimited_credits(self):
        profile = self.store.ensure_organization(1, unlimited=True)
        self.assertTrue(profile["unlimited_credits"])
        self.assertEqual(profile["plan_code"], "internal")
        company_list = self.store.create_company_list(1, 7, name="Clientes")
        result = self.store.add_companies(1, company_list["id"], 7, [COMPANY_A, COMPANY_B])
        self.assertEqual(result["credits_spent"], 0)
        self.assertEqual(result["total"], 2)
        self.assertEqual(self.store.billing_summary(1)["unlocked_companies"], 2)

    def test_trial_credits_are_spent_only_on_first_unlock(self):
        self.store.ensure_organization(2, initial_credits=2)
        first = self.store.create_company_list(2, 9, name="Primeira")
        second = self.store.create_company_list(2, 9, name="Segunda")
        initial = self.store.billing_summary(2)
        self.assertEqual(initial["profile"]["credit_balance"], 2)
        result = self.store.add_companies(2, first["id"], 9, [COMPANY_A])
        self.assertEqual(result["credits_spent"], 1)
        repeat = self.store.add_companies(2, second["id"], 9, [COMPANY_A])
        self.assertEqual(repeat["credits_spent"], 0)
        final = self.store.billing_summary(2)
        self.assertEqual(final["profile"]["credit_balance"], 1)
        self.assertEqual(final["unlocked_companies"], 1)

    def test_insufficient_credits_rolls_back_entire_selection(self):
        self.store.ensure_organization(3, initial_credits=1)
        company_list = self.store.create_company_list(3, 10, name="Alvos")
        with self.assertRaises(SaaSError) as raised:
            self.store.add_companies(3, company_list["id"], 10, [COMPANY_A, COMPANY_B])
        self.assertEqual(raised.exception.status, 402)
        self.assertEqual(self.store.company_list_detail(3, company_list["id"])["companies"], [])
        self.assertEqual(self.store.billing_summary(3)["profile"]["credit_balance"], 1)

    def test_lists_and_saved_searches_are_isolated_by_organization(self):
        self.store.ensure_organization(4)
        self.store.ensure_organization(5)
        saved = self.store.create_saved_search(
            4, 11, name="Autopeças SP", filters={"ufs": ["SP"], "limit": 100}
        )
        company_list = self.store.create_company_list(4, 11, name="Lista privada")
        self.assertIsNone(self.store.saved_search(5, saved["id"]))
        self.assertIsNone(self.store.company_list(5, company_list["id"]))
        self.assertEqual(self.store.list_saved_searches(5), [])
        self.assertEqual(self.store.list_company_lists(5), [])

    def test_saved_search_records_last_run_and_can_be_deleted(self):
        self.store.ensure_organization(6)
        saved = self.store.create_saved_search(
            6, 12, name="Indústrias", filters={"cnaes": ["1091102"], "limit": 250}
        )
        updated = self.store.record_saved_search_run(6, saved["id"], 87)
        self.assertEqual(updated["last_result_count"], 87)
        self.assertIsNotNone(updated["last_run_at"])
        self.assertTrue(self.store.delete_saved_search(6, saved["id"]))
        self.assertFalse(self.store.delete_saved_search(6, saved["id"]))

    def test_credit_grants_are_idempotent(self):
        self.store.ensure_organization(7, initial_credits=0)
        self.store.grant_credits(7, 500, description="Pagamento", idempotency_key="payment:abc")
        self.store.grant_credits(7, 500, description="Pagamento", idempotency_key="payment:abc")
        summary = self.store.billing_summary(7)
        self.assertEqual(summary["profile"]["credit_balance"], 500)
        self.assertEqual(len(summary["ledger"]), 1)


if __name__ == "__main__":
    unittest.main()
