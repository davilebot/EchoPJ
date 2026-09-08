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

    def test_credit_estimate_and_export_unlock_are_consistent(self):
        self.store.ensure_organization(8, initial_credits=2)
        estimate = self.store.credit_estimate(8, [COMPANY_A["cnpj"], COMPANY_B["cnpj"], COMPANY_A["cnpj"]])
        self.assertEqual(estimate["requested_companies"], 2)
        self.assertEqual(estimate["credits_required"], 2)
        self.assertTrue(estimate["can_complete"])
        first = self.store.unlock_companies(
            8, 13, [COMPANY_A["cnpj"], COMPANY_B["cnpj"]],
            kind="company_export", description="Exportação",
        )
        self.assertEqual(first["credits_spent"], 2)
        repeat = self.store.unlock_companies(
            8, 13, [COMPANY_A["cnpj"]], kind="company_export", description="Nova exportação",
        )
        self.assertEqual(repeat["credits_spent"], 0)
        self.assertEqual(self.store.credit_estimate(8, [COMPANY_A["cnpj"]])["credits_required"], 0)

    def test_export_unlock_rolls_back_when_balance_is_insufficient(self):
        self.store.ensure_organization(9, initial_credits=1)
        with self.assertRaises(SaaSError) as raised:
            self.store.unlock_companies(
                9, 14, [COMPANY_A["cnpj"], COMPANY_B["cnpj"]],
                kind="company_export", description="Exportação",
            )
        self.assertEqual(raised.exception.status, 402)
        summary = self.store.billing_summary(9)
        self.assertEqual(summary["profile"]["credit_balance"], 1)
        self.assertEqual(summary["unlocked_companies"], 0)

    def test_dashboard_counts_resources_and_recent_items(self):
        self.store.ensure_organization(10, initial_credits=3)
        company_list = self.store.create_company_list(10, 15, name="Clientes")
        self.store.add_companies(10, company_list["id"], 15, [COMPANY_A])
        self.store.create_saved_search(10, 15, name="Indústrias", filters={"cnaes": ["1091102"]})
        dashboard = self.store.dashboard_summary(10)
        self.assertEqual(dashboard["list_count"], 1)
        self.assertEqual(dashboard["saved_search_count"], 1)
        self.assertEqual(dashboard["unlocked_companies"], 1)
        self.assertEqual(dashboard["recent_lists"][0]["company_count"], 1)

    def test_payment_idempotency_key_cannot_cross_organizations(self):
        self.store.ensure_organization(11, initial_credits=0)
        self.store.ensure_organization(12, initial_credits=0)
        self.store.grant_credits(11, 20, description="Pagamento", idempotency_key="payment:shared")
        with self.assertRaises(SaaSError) as raised:
            self.store.grant_credits(12, 20, description="Pagamento", idempotency_key="payment:shared")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.store.billing_summary(12)["profile"]["credit_balance"], 0)

    def test_admin_can_update_profile_and_adjust_credits_with_ledger(self):
        self.store.ensure_organization(13, initial_credits=10)
        profile = self.store.update_billing_profile(
            13, plan_code="growth", subscription_status="past_due", unlimited_credits=False,
        )
        self.assertEqual((profile["plan_code"], profile["subscription_status"]), ("growth", "past_due"))
        adjusted = self.store.adjust_credits(13, 25, description="Ajuste comercial", actor_id=7)
        self.assertEqual(adjusted["credit_balance"], 35)
        summary = self.store.billing_summary(13)
        self.assertEqual(summary["ledger"][0]["kind"], "manual_adjustment")
        self.assertEqual(summary["ledger"][0]["delta"], 25)
        with self.assertRaises(SaaSError):
            self.store.adjust_credits(13, -36, description="Ajuste inválido", actor_id=7)
        self.assertEqual(self.store.billing_summary(13)["profile"]["credit_balance"], 35)

    def test_unlimited_profile_rejects_manual_balance_adjustment(self):
        self.store.ensure_organization(14, unlimited=True)
        with self.assertRaises(SaaSError) as raised:
            self.store.adjust_credits(14, 10, description="Não aplicável", actor_id=7)
        self.assertEqual(raised.exception.status, 409)

    def test_admin_metrics_summarize_commercial_operation(self):
        self.store.ensure_organization(15, initial_credits=4)
        self.store.ensure_organization(16, initial_credits=6)
        self.store.update_billing_profile(16, plan_code="trial", subscription_status="suspended", unlimited_credits=False)
        metrics = self.store.admin_metrics()
        self.assertEqual(metrics["configured_organizations"], 2)
        self.assertEqual(metrics["active_organizations"], 1)
        self.assertEqual(metrics["credits_available"], 10)


if __name__ == "__main__":
    unittest.main()
