import unittest
from types import SimpleNamespace

from service.launch import commercial_launch_readiness


class CommercialLaunchReadinessTests(unittest.TestCase):
    def settings(self, **updates):
        values = {
            "app_public_url": "https://echopjs-saas-v2.example.com",
            "asaas_api_url": "https://api.asaas.com",
            "saas_billing_enabled": True,
            "saas_self_signup_enabled": True,
            "saas_offsite_backup_configured": True,
            "saas_external_alerts_configured": True,
        }
        values.update(updates)
        return SimpleNamespace(**values)

    def report(self, settings=None, **updates):
        values = {
            "application_ready": True,
            "backup": {"status": "ok"},
            "email_ready": True,
            "legal_ready": True,
            "billing_catalog_ready": True,
            "billing_provider_ready": True,
        }
        values.update(updates)
        return commercial_launch_readiness(settings or self.settings(), **values)

    def test_all_required_launch_conditions_produce_ready_state(self):
        report = self.report()
        self.assertTrue(report["ready"])
        self.assertEqual(report["ready_count"], report["total_count"])
        self.assertEqual(report["blocker_count"], 0)
        self.assertEqual(len({item["key"] for item in report["checks"]}), report["total_count"])

    def test_default_host_sandbox_and_missing_controls_remain_visible(self):
        settings = self.settings(
            app_public_url="https://echopjs-saas-v2.ztnbow.easypanel.host",
            asaas_api_url="https://api-sandbox.asaas.com",
            saas_billing_enabled=False,
            saas_self_signup_enabled=False,
            saas_offsite_backup_configured=False,
            saas_external_alerts_configured=False,
        )
        report = self.report(
            settings,
            backup={"status": "stale"}, email_ready=False, legal_ready=False,
            billing_catalog_ready=False, billing_provider_ready=False,
        )
        self.assertFalse(report["ready"])
        states = {item["key"]: item["ready"] for item in report["checks"]}
        for key in ("domain", "email", "legal", "signup", "catalog", "billing", "backup", "offsite_backup", "external_alerts"):
            self.assertFalse(states[key])
        serialized = str(report)
        self.assertNotIn("api_key", serialized.casefold())
        self.assertNotIn("webhook_token", serialized.casefold())


if __name__ == "__main__":
    unittest.main()
