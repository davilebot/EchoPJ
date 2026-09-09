"""Exercise the complete ASGI middleware/routing/dependency chain without PostgreSQL."""
import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from service.auth import AuthStore
from service.jobs import JobStore
from service.legal import LegalDocuments
from service.limits import SlidingWindowRateLimiter
from service.payments import BillingCatalog
from service.saas import SaaSStore


class OrganizationAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial = tempfile.TemporaryDirectory()
        from service.config import get_settings
        get_settings.cache_clear()
        with patch.dict(os.environ, {
            "POSTGRES_DSN": "postgresql://test@127.0.0.1/test",
            "AUTH_DATABASE_PATH": f"{cls.initial.name}/auth.sqlite",
            "SAAS_DATABASE_PATH": f"{cls.initial.name}/saas.sqlite",
            "JOB_DATABASE_PATH": f"{cls.initial.name}/jobs.sqlite",
            "WEBSITE_CACHE_PATH": f"{cls.initial.name}/web.sqlite",
            "APP_USERNAME": "test", "APP_PASSWORD": "test-only-password",
        }):
            cls.main = importlib.import_module("service.main")
        cls.main.auth_store.close(); cls.main.job_store.close(); cls.main.website_checker.close()
        get_settings.cache_clear()

    @classmethod
    def tearDownClass(cls):
        cls.initial.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(f"{self.tmp.name}/auth.sqlite")
        self.jobs = JobStore(f"{self.tmp.name}/jobs.sqlite")
        self.saas = SaaSStore(f"{self.tmp.name}/saas.sqlite")
        self.auth.bootstrap("owner@example.com", "test-only-password")
        self.owner = self.auth.authenticate("owner@example.com", "test-only-password")
        self.org = self.auth.ensure_initial_organization()
        self.other = self.auth.create_organization(self.owner["id"], "Other")["id"]
        self.saas.ensure_organization(self.org, unlimited=True)
        self.saas.ensure_organization(self.other, initial_credits=2)
        self.owner_token = self.auth.create_session(self.owner["id"])[0]
        invite = self.auth.create_invitation(self.owner["id"], self.org, "member@example.com", "member")
        self.member = self.auth.accept_invitation(invite["token"], "member-password")[0]
        self.member_token = self.auth.create_session(self.member["id"])[0]
        self.legal = LegalDocuments(
            operator_name="Echo Teste Ltda.", operator_document="00.000.000/0001-00",
            operator_address="Rua Teste, 100, São Paulo - SP", contact_email="contato@example.com",
            privacy_email="privacidade@example.com", terms_version="2026-09",
            privacy_version="2026-09", effective_date="2026-09-09",
            retention_policy="Dados de conta são mantidos durante o contrato e pelo prazo legal aplicável.",
        )
        self.patches = [patch.object(self.main, "auth_store", self.auth), patch.object(self.main, "job_store", self.jobs), patch.object(self.main, "saas_store", self.saas), patch.object(self.main, "repository", MagicMock()), patch.object(self.main, "heavy_rate_limiter", SlidingWindowRateLimiter(requests=1000)), patch.object(self.main.job_runner, "notify"), patch.object(self.main, "legal_documents", self.legal)]
        for mock in self.patches: mock.start()

    def tearDown(self):
        for mock in reversed(self.patches): mock.stop()
        self.auth.close(); self.jobs.close(); self.saas.close(); self.tmp.cleanup()

    def request(self, path, method="GET", data=None, token=None, headers=None):
        parts = urlsplit(path)
        body = json.dumps(data).encode() if data is not None else b""
        request_headers = {"host": "testserver", "content-type": "application/json", **(headers or {})}
        if token: request_headers["cookie"] = f"echopjs_session={token}"
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": method, "scheme": "https", "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(), "root_path": "", "headers": [(k.lower().encode(), v.encode()) for k, v in request_headers.items()], "client": ("127.0.0.1", 12345), "server": ("testserver", 443)}
        messages = []
        async def run():
            delivered = False
            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await asyncio.Event().wait()
            async def send(message): messages.append(message)
            await self.main.app(scope, receive, send)
        asyncio.run(run())
        response = next(m for m in messages if m["type"] == "http.response.start")
        content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        try: payload = json.loads(content)
        except (ValueError, UnicodeError): payload = content.decode(errors="replace")
        return response["status"], payload, dict(response["headers"])

    def test_login_session_and_organization_listing(self):
        status, _, headers = self.request("/api/auth/login", "POST", {"identifier": "owner@example.com", "password": "test-only-password"})
        self.assertEqual(status, 200)
        self.assertIn(b"Secure", headers[b"set-cookie"])
        self.assertIn(b"HttpOnly", headers[b"set-cookie"])
        self.assertIn(b"x-request-id", headers)
        self.assertIn(b"x-response-time-ms", headers)
        self.assertEqual(self.request("/api/organizations")[0], 401)
        status, data, _ = self.request("/api/organizations", token=self.member_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["organizations"]), 1)
        self.assertFalse(data["can_create"])

    def test_account_privacy_export_and_deletion_workflow_are_authenticated_and_auditable(self):
        self.assertEqual(self.request("/api/privacy")[0], 401)
        status, summary, _ = self.request("/api/privacy", token=self.owner_token)
        self.assertEqual(status, 200)
        self.assertIsNone(summary["deletion_request"])

        self.assertEqual(self.request(
            "/api/privacy/export", "POST", {"current_password": "wrong"}, self.owner_token,
        )[0], 401)
        status, exported, headers = self.request(
            "/api/privacy/export", "POST", {"current_password": "test-only-password"}, self.owner_token,
        )
        self.assertEqual(status, 200)
        self.assertIn(b"attachment", headers[b"content-disposition"])
        self.assertEqual(exported["account"]["identifier"], "owner@example.com")
        self.assertNotIn("password", json.dumps(exported))
        self.assertNotIn("token", json.dumps(exported))

        status, request, _ = self.request(
            "/api/privacy/deletion-requests", "POST",
            {"current_password": "test-only-password", "reason": "Conta sem uso"}, self.owner_token,
        )
        self.assertEqual(status, 201)
        self.assertTrue(request["created"])
        self.assertEqual(self.request("/api/admin/privacy/requests", token=self.member_token)[0], 403)
        status, queue, _ = self.request(
            "/api/admin/privacy/requests", token=self.owner_token,
            headers={"x-organization-id": str(self.org)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(queue["requests"][0]["id"], request["id"])
        status, reviewed, _ = self.request(
            f"/api/admin/privacy/requests/{request['id']}", "PATCH",
            {"status": "in_review", "resolution_note": "Validando vínculos"}, self.owner_token,
            {"x-organization-id": str(self.org)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reviewed["status"], "in_review")
        self.assertEqual(
            self.request("/api/privacy/deletion-requests/current", "DELETE", token=self.owner_token)[1]["status"],
            "canceled",
        )

    def test_liveness_readiness_and_internal_operations_are_distinct(self):
        self.assertEqual(self.request("/health/live")[0:2], (200, {"status": "ok"}))
        with (
            patch.object(self.main.repository, "health_check", return_value={"ok": True, "dataset_version": "2026-08"}),
            patch.object(self.auth, "health_check", return_value=True),
            patch.object(self.saas, "health_check", return_value=True),
            patch.object(self.jobs, "health_check", return_value=True),
            patch.object(self.main.job_runner, "is_alive", return_value=True),
        ):
            status, ready, _ = self.request("/health/ready")
            self.assertEqual(status, 200)
            self.assertTrue(ready["ready"])
            status, operations, _ = self.request(
                "/api/admin/operations", token=self.owner_token,
                headers={"x-organization-id": str(self.org)},
            )
            self.assertEqual(status, 200)
            self.assertIn("traffic", operations)
            self.assertIn("backup", operations)
            self.assertIn("launch", operations)
            self.assertEqual(operations["launch"]["total_count"], 10)
            self.assertTrue(any(item["key"] == "legal" for item in operations["launch"]["checks"]))
        with patch.object(self.main.repository, "health_check", side_effect=RuntimeError("offline")):
            self.assertEqual(self.request("/health/ready")[0], 503)
        self.assertEqual(self.request("/api/admin/operations", token=self.member_token)[0], 403)

    def test_password_recovery_is_generic_single_use_and_logs_in(self):
        existing = self.request(
            "/api/auth/password-reset/request", "POST", {"identifier": "owner@example.com"},
        )
        missing = self.request(
            "/api/auth/password-reset/request", "POST", {"identifier": "missing@example.com"},
        )
        self.assertEqual(existing[:2], (202, {"accepted": True}))
        self.assertEqual(missing[:2], (202, {"accepted": True}))

        reset = self.auth.create_password_reset("owner@example.com")
        status, payload, headers = self.request(
            "/api/auth/password-reset/confirm", "POST",
            {"token": reset["token"], "new_password": "new-secure-password"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["reset"])
        self.assertIn(b"set-cookie", headers)
        self.assertIsNone(self.auth.user_for_session(self.owner_token))
        self.assertIsNotNone(self.auth.authenticate("owner@example.com", "new-secure-password"))
        self.assertEqual(
            self.request(
                "/api/auth/password-reset/confirm", "POST",
                {"token": reset["token"], "new_password": "another-secure-password"},
            )[0],
            404,
        )

    def test_verified_signup_creates_trial_workspace_and_session(self):
        with (
            patch.object(self.main.settings, "saas_self_signup_enabled", True),
            patch.object(self.main, "mail_available", return_value=True),
            patch.object(self.main, "send_signup_verification") as sender,
        ):
            status, signup_status, _ = self.request("/api/auth/status")
            self.assertEqual(status, 200)
            self.assertTrue(signup_status["signup_available"])
            self.assertTrue(signup_status["legal"]["configured"])
            self.assertEqual(self.request(
                "/api/auth/signup", "POST",
                {"name": "Nova Empresa", "email": "new@example.com", "password": "secure-password", "accept_terms": True, "terms_version": "old", "privacy_version": "2026-09"},
            )[0], 409)
            status, pending, _ = self.request(
                "/api/auth/signup", "POST",
                {"name": "Nova Empresa", "email": "new@example.com", "password": "secure-password", "accept_terms": True, "terms_version": "2026-09", "privacy_version": "2026-09"},
            )
            self.assertEqual(status, 202)
            self.assertEqual(pending["email"], "new@example.com")
            sender.assert_called_once()
            link = sender.call_args.kwargs["link"]
            token = urlsplit(link).fragment.removeprefix("token=")

        status, verified, headers = self.request(
            "/api/auth/signup/verify", "POST", {"token": token},
        )
        self.assertEqual(status, 200)
        self.assertIn(b"set-cookie", headers)
        organization_id = verified["organization_id"]
        self.assertEqual(self.auth.authenticate("new@example.com", "secure-password")["identifier"], "new@example.com")
        session_token = headers[b"set-cookie"].decode().split("echopjs_session=", 1)[1].split(";", 1)[0]
        self.assertEqual(len(self.request("/api/legal/acceptances", token=session_token)[1]["acceptances"]), 2)
        self.assertEqual(self.saas.billing_summary(organization_id)["profile"]["credit_balance"], self.main.settings.saas_trial_credits)
        self.assertEqual(self.request("/api/auth/signup/verify", "POST", {"token": token})[0], 404)

    def test_signup_remains_closed_until_legal_documents_are_configured(self):
        draft = LegalDocuments("", "", "", "", "", "", "", "", "")
        with (
            patch.object(self.main.settings, "saas_self_signup_enabled", True),
            patch.object(self.main, "mail_available", return_value=True),
            patch.object(self.main, "legal_documents", draft),
        ):
            status, payload, _ = self.request("/api/auth/status")
            self.assertEqual(status, 200)
            self.assertFalse(payload["signup_available"])
            self.assertEqual(payload["signup_blocker"], "legal")
            self.assertFalse(self.request("/api/legal/documents")[1]["configured"])
            self.assertEqual(self.request(
                "/api/auth/signup", "POST",
                {"name": "Nova Empresa", "email": "new@example.com", "password": "secure-password", "accept_terms": True, "terms_version": "2026-09", "privacy_version": "2026-09"},
            )[0], 503)

    def test_internal_admin_can_manage_commercial_profiles(self):
        headers = {"x-organization-id": str(self.org)}
        self.saas.record_product_event(
            self.other, self.owner["id"], "search.executed",
            occurred_at="2026-08-10T10:00:00+00:00",
        )
        self.saas.record_product_event(
            self.other, self.owner["id"], "search.executed",
            occurred_at="2026-08-11T10:00:00+00:00",
        )
        status, overview, _ = self.request(
            "/api/admin/overview", token=self.owner_token, headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(overview["total"], 2)
        self.assertEqual(overview["user_count"], 2)
        self.assertTrue(any(item["id"] == self.other for item in overview["organizations"]))
        self.assertEqual(overview["funnel"]["registered_organizations"], 1)
        self.assertEqual(overview["funnel"]["activated_organizations"], 1)
        self.assertEqual(overview["funnel"]["retained_organizations"], 1)
        self.assertFalse(overview["billing_emails"]["enabled"])
        self.assertEqual(overview["billing_emails"]["deliveries"], [])
        other = next(item for item in overview["organizations"] if item["id"] == self.other)
        self.assertEqual(other["activity"]["stage"], "activated")

        status, profile, _ = self.request(
            f"/api/admin/organizations/{self.other}/billing", "PATCH",
            {"plan_code": "growth", "subscription_status": "active", "unlimited_credits": False},
            self.owner_token, headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(profile["plan_code"], "growth")
        detail = self.request(
            f"/api/admin/organizations/{self.other}", token=self.owner_token, headers=headers,
        )[1]
        self.assertTrue(detail["activity"]["converted"])
        status, adjusted, _ = self.request(
            f"/api/admin/organizations/{self.other}/credits", "POST",
            {"amount": 8, "description": "Crédito comercial"}, self.owner_token, headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(adjusted["credit_balance"], 10)
        self.assertEqual(
            self.request(
                f"/api/admin/organizations/{self.org}/billing", "PATCH",
                {"plan_code": "trial", "subscription_status": "active", "unlimited_credits": False},
                self.owner_token, headers,
            )[0],
            409,
        )

    def test_internal_member_cannot_access_saas_administration(self):
        self.assertEqual(
            self.request(
                "/api/admin/overview", token=self.member_token,
                headers={"x-organization-id": str(self.org)},
            )[0],
            403,
        )

    def test_internal_admin_can_draft_and_publish_versioned_billing_catalog(self):
        headers = {"x-organization-id": str(self.org)}
        offer = {
            "code": "growth", "name": "Crescimento", "kind": "subscription",
            "price_cents": 14990, "credits": 1000, "cycle": "MONTHLY",
            "description": "Créditos mensais para prospecção recorrente.",
            "features": ["1.000 créditos por mês", "Saldo compartilhado"],
            "highlighted": True,
        }
        self.assertEqual(self.request(
            "/api/admin/billing/catalog", token=self.member_token,
            headers=headers,
        )[0], 403)
        status, draft_state, _ = self.request(
            "/api/admin/billing/catalog/draft", "PUT", {"offers": [offer]},
            self.owner_token, headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(draft_state["draft"]["revision"], 1)
        self.assertEqual(draft_state["active"]["offers"], [])
        self.assertFalse(self.request("/api/billing/catalog")[1]["configured"])
        duplicate_highlight = {**offer, "code": "scale", "name": "Escala"}
        self.assertEqual(self.request(
            "/api/admin/billing/catalog/draft", "PUT", {"offers": [offer, duplicate_highlight]},
            self.owner_token, headers,
        )[0], 422)
        status, published, _ = self.request(
            "/api/admin/billing/catalog/publish", "POST", token=self.owner_token, headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(published["source"], "published")
        self.assertIsNone(published["draft"])
        public = self.request("/api/billing/catalog")[1]
        self.assertTrue(public["configured"])
        self.assertEqual((public["offers"][0]["code"], public["offers"][0]["price_cents"]), ("growth", 14990))
        revised = {**offer, "price_cents": 17990}
        self.assertEqual(self.request(
            "/api/admin/billing/catalog/draft", "PUT", {"offers": [revised]},
            self.owner_token, headers,
        )[1]["draft"]["revision"], 2)
        self.assertEqual(self.request("/api/billing/catalog")[1]["offers"][0]["price_cents"], 14990)

    def test_checkout_is_admin_only_and_webhook_grants_once_after_payment(self):
        catalog = BillingCatalog(json.dumps([{
            "code": "growth", "name": "Crescimento", "kind": "subscription",
            "price_cents": 14990, "credits": 1000, "cycle": "MONTHLY",
            "description": "Créditos mensais para prospecção.", "features": ["1.000 créditos"],
        }]))
        provider = MagicMock()
        provider.available = True
        provider.create_checkout.return_value = {
            "provider_checkout_id": "chk_api",
            "checkout_url": "https://sandbox.asaas.com/checkout/api",
        }
        headers = {"x-organization-id": str(self.other), "idempotency-key": "checkout-api-1"}
        with (
            patch.object(self.main, "billing_catalog", catalog),
            patch.object(self.main, "asaas_client", provider),
            patch.object(self.main.settings, "saas_billing_enabled", True),
            patch.object(self.main.settings, "saas_billing_provider", "asaas"),
            patch.object(self.main.settings, "asaas_webhook_token", "webhook-secret-with-at-least-32-chars"),
        ):
            self.assertEqual(
                self.request("/api/billing/checkouts", "POST", {"plan_code": "growth"}, self.member_token, {"x-organization-id": str(self.org)})[0],
                403,
            )
            status, checkout, _ = self.request(
                "/api/billing/checkouts", "POST", {"plan_code": "growth"}, self.owner_token, headers,
            )
            self.assertEqual(status, 201)
            self.assertEqual(checkout["checkout_url"], "https://sandbox.asaas.com/checkout/api")
            retry = self.request(
                "/api/billing/checkouts", "POST", {"plan_code": "growth"}, self.owner_token, headers,
            )
            self.assertEqual(retry[0], 201)
            self.assertEqual(retry[1]["id"], checkout["id"])
            duplicate_headers = {"x-organization-id": str(self.other), "idempotency-key": "checkout-api-2"}
            self.assertEqual(
                self.request(
                    "/api/billing/checkouts", "POST", {"plan_code": "growth"}, self.owner_token, duplicate_headers,
                )[0],
                409,
            )
            provider.create_checkout.assert_called_once()
            order = self.saas.billing_summary(self.other)["orders"][0]
            event = {
                "id": "evt_api_1", "event": "PAYMENT_RECEIVED",
                "payment": {"id": "pay_api_1", "subscription": "sub_api_1", "externalReference": f"echopjs-order:{order['id']}", "value": 149.9},
            }
            self.assertEqual(self.request("/api/webhooks/asaas", "POST", event)[0], 401)
            webhook_headers = {"asaas-access-token": "webhook-secret-with-at-least-32-chars"}
            self.assertEqual(self.request("/api/webhooks/asaas", "POST", event, headers=webhook_headers)[0], 200)
            duplicate = self.request("/api/webhooks/asaas", "POST", event, headers=webhook_headers)[1]
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(
                self.request(
                    "/api/billing/subscription", "DELETE",
                    {"current_password": "wrong", "reason": "Sem uso"}, self.owner_token, headers,
                )[0],
                401,
            )
            provider.cancel_subscription.return_value = {
                "provider_subscription_id": "sub_api_1", "canceled": True,
            }
            status, canceled, _ = self.request(
                "/api/billing/subscription", "DELETE",
                {"current_password": "test-only-password", "reason": "Sem uso"}, self.owner_token, headers,
            )
            self.assertEqual(status, 200)
            self.assertTrue(canceled["canceled"])
            provider.cancel_subscription.assert_called_once_with("sub_api_1")
        self.assertEqual(self.saas.billing_summary(self.other)["profile"]["credit_balance"], 1002)
        self.assertEqual(self.saas.billing_summary(self.other)["profile"]["plan_code"], "growth")
        self.assertEqual(self.saas.billing_summary(self.other)["profile"]["subscription_status"], "canceled")

    def test_suspended_customer_workspace_is_blocked(self):
        self.saas.update_billing_profile(
            self.other, plan_code="growth", subscription_status="suspended", unlimited_credits=False,
        )
        status, payload, _ = self.request(
            "/api/dashboard", token=self.owner_token,
            headers={"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 403)
        self.assertIn("suspenso", payload["detail"])
        self.assertEqual(
            self.request(
                "/api/admin/overview", token=self.owner_token,
                headers={"x-organization-id": str(self.org)},
            )[0],
            200,
        )

    def test_tenant_history_and_export_enforced_for_owner_of_both_orgs(self):
        job = self.jobs.create_job("private.csv", [], active_only=True, check_website=False, organization_id=self.org)
        status, data, _ = self.request("/api/jobs", token=self.owner_token, headers={"x-organization-id": str(self.other)})
        self.assertEqual(status, 200); self.assertEqual(data["jobs"], [])
        for suffix in ("", "/export.csv"):
            self.assertEqual(self.request(f"/api/jobs/{job['id']}{suffix}?organization_id={self.other}", token=self.owner_token)[0], 404)
        self.assertEqual(self.request(f"/api/jobs/{job['id']}/export.csv?organization_id={self.org}", token=self.owner_token)[0], 200)
        self.main.repository.companies_by_cnpjs.assert_not_called()

    def test_tampered_org_header_and_paths_cannot_grant_access(self):
        for path in ("/api/jobs", "/api/search/capabilities", "/api/explorer/schema"):
            self.assertEqual(self.request(path, token=self.member_token, headers={"x-organization-id": str(self.other)})[0], 403)
        for path in (f"/api/organizations/{self.org}", f"/api/organizations/{self.other}"):
            self.assertEqual(self.request(path, token=self.member_token)[0], 403)
        self.assertEqual(self.request("/api/organizations", "POST", {"name": "Hacked"}, self.member_token)[0], 403)
        self.assertEqual(self.request(f"/api/organizations/{self.org}/invitations", "POST", {"email": "new@example.com", "role": "admin"}, self.member_token)[0], 403)

    def test_invite_api_creates_account_and_consumes_token(self):
        status, data, _ = self.request(f"/api/organizations/{self.other}/invitations", "POST", {"email": "new@example.com", "send_email": False}, self.owner_token)
        self.assertEqual(status, 201); self.assertEqual(data["delivery"], "manual")
        self.assertIn("#token=", data["link"])
        token = data["link"].split("#token=")[1]
        status, preview, _ = self.request("/api/invitations/preview", "POST", {"token": token})
        self.assertEqual(status, 200); self.assertEqual(preview["email"], "new@example.com")
        self.assertTrue(preview["legal_acceptance_required"])
        self.assertEqual(preview["legal"]["documents"]["terms"]["version"], "2026-09")
        self.assertEqual(self.request("/api/invitations/accept", "POST", {"token": token, "password": "new-password-long"})[0], 422)
        acceptance = {"token": token, "password": "new-password-long", "accept_terms": True, "terms_version": "2026-09", "privacy_version": "2026-09"}
        status, data, headers = self.request("/api/invitations/accept", "POST", acceptance)
        self.assertEqual(status, 200); self.assertEqual(data["organization_id"], self.other)
        self.assertIn(b"set-cookie", headers)
        invited = self.auth.authenticate("new@example.com", "new-password-long")
        self.assertEqual({item["source"] for item in self.auth.legal_acceptances(invited["id"])}, {"invitation"})
        self.assertEqual(self.request("/api/invitations/accept", "POST", acceptance)[0], 404)

    def test_removed_members_session_immediately_loses_org_access(self):
        self.auth.change_member(self.owner["id"], self.org, self.member["id"])
        self.assertEqual(self.request("/api/jobs", token=self.member_token)[0], 403)
        self.assertEqual(self.request("/api/auth/me", token=self.member_token)[0], 200)

    def test_owner_can_transfer_customer_workspace_then_leave_it(self):
        invitation = self.auth.create_invitation(self.owner["id"], self.other, "client@example.com", "admin")
        client = self.auth.accept_invitation(invitation["token"], "client-password-long")[0]
        self.assertEqual(self.request(
            f"/api/organizations/{self.org}/owner/{self.member['id']}", "PUT",
            token=self.owner_token,
        )[0], 409)
        status, transferred, _ = self.request(
            f"/api/organizations/{self.other}/owner/{client['id']}", "PUT",
            token=self.owner_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(transferred["owner"]["created_by"], client["id"])
        headers = {"x-organization-id": str(self.org)}
        self.assertEqual(
            self.request(f"/api/admin/organizations/{self.other}", token=self.owner_token, headers=headers)[1]["owner_email"],
            "client@example.com",
        )
        self.assertEqual(self.request(
            f"/api/organizations/{self.other}/members/{self.owner['id']}", "DELETE",
            token=self.owner_token,
        )[0], 200)
        self.assertEqual(self.request(f"/api/organizations/{self.other}", token=self.owner_token)[0], 403)

    def test_internal_admin_can_prepare_complete_customer_pilot(self):
        internal_headers = {"x-organization-id": str(self.org)}
        self.assertEqual(self.request(
            "/api/admin/customer-workspaces", "POST",
            {"name": "Tentativa", "owner_email": "blocked@example.com", "trial_credits": 10},
            self.member_token, internal_headers,
        )[0], 403)
        status, pilot, _ = self.request(
            "/api/admin/customer-workspaces", "POST",
            {
                "name": "Cliente Piloto", "owner_email": "cliente.piloto@example.com",
                "trial_credits": 37, "send_email": False,
            },
            self.owner_token, internal_headers,
        )
        self.assertEqual(status, 201)
        organization_id = pilot["organization"]["id"]
        self.assertEqual(pilot["organization"]["billing"]["credit_balance"], 37)
        self.assertEqual(pilot["organization"]["billing"]["plan_code"], "trial")
        self.assertEqual(pilot["invitation"]["delivery"], "manual")
        self.assertEqual(pilot["invitation"]["role"], "admin")
        self.assertNotIn("token", pilot["invitation"])
        self.assertIn("/invite#token=", pilot["invitation"]["link"])
        status, overview, _ = self.request(
            "/api/admin/overview?query=cliente.piloto%40example.com", token=self.owner_token,
            headers=internal_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(overview["organizations"][0]["provisioning"]["status"], "waiting_acceptance")
        original_token = pilot["invitation"]["link"].split("#token=")[1]
        self.assertEqual(self.request(
            f"/api/admin/organizations/{organization_id}/pilot-invitation", "POST",
            {"send_email": False}, self.member_token, internal_headers,
        )[0], 403)
        status, replacement, _ = self.request(
            f"/api/admin/organizations/{organization_id}/pilot-invitation", "POST",
            {"send_email": False}, self.owner_token, internal_headers,
        )
        self.assertEqual(status, 201)
        self.assertEqual(replacement["delivery"], "manual")
        self.assertNotIn("token", replacement)
        self.assertEqual(self.request("/api/invitations/preview", "POST", {"token": original_token})[0], 404)
        token = replacement["link"].split("#token=")[1]
        preview = self.request("/api/invitations/preview", "POST", {"token": token})[1]
        self.assertEqual(preview["organization_name"], "Cliente Piloto")
        acceptance = {
            "token": token, "password": "client-password-long", "accept_terms": True,
            "terms_version": "2026-09", "privacy_version": "2026-09",
        }
        status, accepted, _ = self.request("/api/invitations/accept", "POST", acceptance)
        self.assertEqual(status, 200)
        self.assertEqual(accepted["organization_id"], organization_id)
        client = self.auth.authenticate("cliente.piloto@example.com", "client-password-long")
        before_transfer = self.request(
            f"/api/admin/organizations/{organization_id}", token=self.owner_token,
            headers=internal_headers,
        )[1]
        self.assertEqual(before_transfer["provisioning"]["status"], "transfer_pending")
        self.assertEqual(before_transfer["provisioning"]["responsible_user_id"], client["id"])
        status, transferred, _ = self.request(
            f"/api/organizations/{organization_id}/owner/{client['id']}", "PUT",
            token=self.owner_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(transferred["owner"]["created_by"], client["id"])
        detail = self.request(
            f"/api/admin/organizations/{organization_id}", token=self.owner_token,
            headers=internal_headers,
        )[1]
        self.assertEqual(detail["owner_email"], "cliente.piloto@example.com")
        self.assertEqual(detail["provisioning"]["status"], "delivered")
        self.assertEqual(self.request(
            f"/api/admin/organizations/{organization_id}/pilot-invitation", "POST",
            {"send_email": False}, self.owner_token, internal_headers,
        )[0], 409)

    def test_jobs_created_are_bound_to_actor_and_org(self):
        payload = {"filename": "test.csv", "items": [{"local_id": "1", "name": "Example", "uf": "SP"}], "check_website": False}
        status, job, _ = self.request("/api/jobs", "POST", payload, self.owner_token, {"x-organization-id": str(self.other)})
        self.assertEqual(status, 200)
        self.assertEqual(job["organization_id"], self.other)
        self.assertEqual(job["created_by"], self.owner["id"])
        self.assertEqual(self.request("/api/jobs", "POST", payload, self.member_token, {"x-organization-id": str(self.other)})[0], 403)

    def test_viewer_can_research_but_cannot_mutate_export_or_spend_credits(self):
        invite = self.auth.create_invitation(self.owner["id"], self.other, "viewer@example.com", "viewer")
        viewer = self.auth.accept_invitation(invite["token"], "viewer-password")[0]
        viewer_token = self.auth.create_session(viewer["id"])[0]
        headers = {"x-organization-id": str(self.other)}
        status, organizations, _ = self.request("/api/organizations", token=viewer_token)
        self.assertEqual(status, 200)
        self.assertEqual(organizations["organizations"][0]["role"], "viewer")
        self.assertFalse(organizations["organizations"][0]["permissions"]["export"])
        self.assertEqual(self.request("/api/dashboard", token=viewer_token, headers=headers)[0], 200)
        self.main.repository.companies_by_cnpjs.return_value = {}
        self.assertEqual(self.request(
            "/api/explorer/company-lookup", "POST", {"cnpjs": ["11222333000181"]}, viewer_token, headers,
        )[0], 200)
        self.assertEqual(self.request("/api/company-lists", token=viewer_token, headers=headers)[0], 200)

        blocked = [
            ("/api/company-lists", {"name": "Bloqueada"}),
            ("/api/saved-searches", {"name": "Bloqueada", "filters": {"limit": 1}}),
            ("/api/exports/companies", {"cnpjs": ["11222333000181"]}),
            ("/api/jobs", {"filename": "blocked.csv", "items": [{"local_id": "1", "name": "Example", "uf": "SP"}]}),
        ]
        for path, payload in blocked:
            status, response, _ = self.request(path, "POST", payload, viewer_token, headers)
            self.assertEqual(status, 403, path)
            self.assertIn("perfil permite consultar", response["detail"])
        saved = self.saas.create_saved_search(
            self.other, self.owner["id"], name="Protegida", filters={"limit": 1},
        )
        status, response, _ = self.request(
            f"/api/saved-searches/{saved['id']}", "PUT",
            {"name": "Tentativa", "filters": {"limit": 1}}, viewer_token, headers,
        )
        self.assertEqual(status, 403)
        self.assertIn("perfil permite consultar", response["detail"])
        company_list = self.saas.create_company_list(
            self.other, self.owner["id"], name="Protegida",
        )
        status, response, _ = self.request(
            f"/api/company-lists/{company_list['id']}", "PUT",
            {"name": "Tentativa"}, viewer_token, headers,
        )
        self.assertEqual(status, 403)
        self.assertIn("perfil permite consultar", response["detail"])
        self.assertEqual(
            self.request(
                f"/api/company-lists/{company_list['id']}/export.csv",
                token=viewer_token, headers=headers,
            )[0],
            403,
        )
        self.assertEqual(self.saas.billing_summary(self.other)["profile"]["credit_balance"], 2)

    def test_csrf_blocked_and_sensitive_input_not_echoed(self):
        self.assertEqual(self.request("/api/organizations", "POST", {"name": "Bad"}, self.owner_token, {"origin": "https://attacker.example"})[0], 403)
        secret = "do-not-echo-this-secret"
        status, payload, _ = self.request("/api/invitations/accept", "POST", {"token": secret, "password": secret})
        self.assertEqual(status, 422); self.assertNotIn(secret, str(payload))

    def test_large_payload_heavy_rate_and_job_queue_are_bounded_per_org(self):
        with patch.object(self.main.settings, "saas_max_request_bytes", 20):
            status, payload, _ = self.request(
                "/api/search", "POST", {"limit": 1}, self.owner_token,
                {"x-organization-id": str(self.org), "content-length": "21"},
            )
        self.assertEqual(status, 413)
        self.assertIn("limite seguro", payload["detail"])

        limiter = SlidingWindowRateLimiter(requests=2, window_seconds=60)
        self.main.repository.companies_by_cnpjs.return_value = {}
        with patch.object(self.main, "heavy_rate_limiter", limiter):
            headers = {"x-organization-id": str(self.org)}
            payload = {"cnpjs": ["11222333000181"]}
            self.assertEqual(self.request("/api/explorer/company-lookup", "POST", payload, self.owner_token, headers)[0], 200)
            self.assertEqual(self.request("/api/explorer/company-lookup", "POST", payload, self.owner_token, headers)[0], 200)
            status, blocked, response_headers = self.request("/api/explorer/company-lookup", "POST", payload, self.owner_token, headers)
        self.assertEqual(status, 429)
        self.assertIn("Muitas operações", blocked["detail"])
        self.assertIn(b"retry-after", response_headers)

        job_payload = {"filename": "test.csv", "items": [{"local_id": "1", "name": "Example", "uf": "SP"}], "check_website": False}
        with patch.object(self.main.settings, "saas_max_active_jobs_per_organization", 1):
            headers = {"x-organization-id": str(self.other)}
            self.assertEqual(self.request("/api/jobs", "POST", job_payload, self.owner_token, headers)[0], 200)
            status, blocked, response_headers = self.request("/api/jobs", "POST", job_payload, self.owner_token, headers)
        self.assertEqual(status, 429)
        self.assertIn("processamentos em andamento", blocked["detail"])
        self.assertEqual(response_headers[b"retry-after"], b"30")

    def test_search_preview_marks_saved_companies_with_organization_lists(self):
        company = {
            "cnpj": "11222333000181",
            "legal_name": "Empresa Exemplo Ltda",
            "primary_cnae": "6201501",
            "primary_cnae_description": "Desenvolvimento de programas",
            "municipality": "São Paulo",
            "uf": "SP",
        }
        company_list = self.saas.create_company_list(self.org, self.owner["id"], name="Prospecção SaaS")
        self.saas.add_companies(self.org, company_list["id"], self.owner["id"], [company])
        capabilities = MagicMock()
        capabilities.as_dict.return_value = {"establishment_details": True}
        self.main.repository.search_companies.return_value = ([company], capabilities, 12, False)
        self.main.repository.current_version.return_value = "2026-08"

        status, data, _ = self.request(
            "/api/search/preview", "POST", {"ufs": ["SP"], "limit": 500}, self.owner_token,
            {"x-organization-id": str(self.org)},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["preview"])
        self.assertEqual(data["limit"], 40)
        self.assertEqual(data["segments"], {"total": 1, "new": 0, "saved": 1})
        self.assertTrue(data["results"][0]["saved"])
        self.assertEqual(data["results"][0]["saved_lists"][0]["name"], "Prospecção SaaS")
        filters = self.main.repository.search_companies.call_args.args[0]
        self.assertEqual(filters["limit"], 40)

        status, foreign_data, _ = self.request(
            "/api/search/preview", "POST", {"ufs": ["SP"], "limit": 500}, self.owner_token,
            {"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(foreign_data["segments"], {"total": 1, "new": 1, "saved": 0})
        self.assertEqual(foreign_data["results"][0]["saved_lists"], [])

    def test_admin_pages_and_membership_free_invite_page(self):
        self.assertEqual(self.request("/organizations")[0], 303)
        self.assertEqual(self.request("/organizations", token=self.owner_token)[0], 200)
        self.assertEqual(self.request("/admin")[0], 303)
        self.assertEqual(self.request("/admin", token=self.owner_token)[0], 200)
        self.assertEqual(self.request("/help")[0], 303)
        help_status, help_html, help_headers = self.request("/help", token=self.owner_token)
        self.assertEqual(help_status, 200); self.assertIn('id="help-search"', help_html)
        self.assertIn(b"no-store", help_headers[b"cache-control"])
        status, html, headers = self.request("/invite")
        self.assertEqual(status, 200); self.assertIn('id="accept-form"', html)
        self.assertEqual(headers[b"referrer-policy"], b"no-referrer")
        self.assertIn(b"no-store", headers[b"cache-control"])
        status, plans, headers = self.request("/plans")
        self.assertEqual(status, 200); self.assertIn('id="plans-grid"', plans)
        self.assertIn(b"no-store", headers[b"cache-control"])
        status, product, headers = self.request("/produto")
        self.assertEqual(status, 200); self.assertIn('id="produto-em-acao"', product)
        self.assertIn(b"no-store", headers[b"cache-control"])

    def test_saas_resources_are_tenant_scoped_and_credits_are_enforced(self):
        saved_payload = {
            "name": "Empresas de SP",
            "filters": {"ufs": ["SP"], "registration_statuses": ["ATIVA"], "limit": 100},
        }
        status, saved, _ = self.request(
            "/api/saved-searches", "POST", saved_payload, self.owner_token,
            {"x-organization-id": str(self.org)},
        )
        self.assertEqual(status, 201)
        status, updated, _ = self.request(
            f"/api/saved-searches/{saved['id']}", "PUT",
            {**saved_payload, "name": "Empresas ativas de SP"}, self.owner_token,
            {"x-organization-id": str(self.org)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "Empresas ativas de SP")
        self.assertEqual(
            self.request(f"/api/saved-searches/{saved['id']}", token=self.owner_token, headers={"x-organization-id": str(self.other)})[0],
            404,
        )
        self.assertEqual(
            self.request(
                f"/api/saved-searches/{saved['id']}", "PUT", saved_payload,
                self.owner_token, {"x-organization-id": str(self.other)},
            )[0],
            404,
        )
        status, company_list, _ = self.request(
            "/api/company-lists", "POST", {"name": "Prospects"}, self.owner_token,
            {"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 201)
        status, company_list, _ = self.request(
            f"/api/company-lists/{company_list['id']}", "PUT",
            {"name": "Prospects prioritários", "description": "Contatar nesta semana"},
            self.owner_token, {"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(company_list["name"], "Prospects prioritários")
        self.assertEqual(company_list["description"], "Contatar nesta semana")
        self.assertEqual(
            self.request(
                f"/api/company-lists/{company_list['id']}", "PUT",
                {"name": "Tentativa externa"}, self.owner_token,
                {"x-organization-id": str(self.org)},
            )[0],
            404,
        )
        companies = [
            {"cnpj": "11222333000181", "legal_name": "Empresa A"},
            {"cnpj": "19131243000197", "legal_name": "Empresa B"},
        ]
        self.main.repository.companies_by_cnpjs.return_value = {
            company["cnpj"]: company for company in companies
        }
        status, result, _ = self.request(
            f"/api/company-lists/{company_list['id']}/companies", "POST", {"cnpjs": [company["cnpj"] for company in companies]},
            self.owner_token, {"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["credits_spent"], 2)
        status, filtered, _ = self.request(
            f"/api/company-lists/{company_list['id']}?q=Empresa%20A&limit=1",
            token=self.owner_token, headers={"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(filtered["filtered_count"], 1)
        self.assertEqual(filtered["companies"][0]["legal_name"], "Empresa A")
        status, exported, export_headers = self.request(
            f"/api/company-lists/{company_list['id']}/export.csv",
            token=self.owner_token, headers={"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 200)
        self.assertIn("Empresa A", exported)
        self.assertIn("Empresa B", exported)
        self.assertEqual(export_headers[b"x-credits-spent"], b"0")
        billing = self.request(
            "/api/billing/summary", token=self.owner_token,
            headers={"x-organization-id": str(self.other)},
        )[1]
        self.assertEqual(billing["profile"]["credit_balance"], 0)
        self.assertEqual(
            self.request(f"/api/company-lists/{company_list['id']}", token=self.owner_token, headers={"x-organization-id": str(self.org)})[0],
            404,
        )

    def test_server_exports_estimate_and_charge_each_company_only_once(self):
        companies = {
            "11222333000181": {"cnpj": "11222333000181", "legal_name": "Empresa A", "partners": []},
            "19131243000197": {"cnpj": "19131243000197", "legal_name": "Empresa B", "partners": []},
        }
        self.main.repository.companies_by_cnpjs.side_effect = lambda cnpjs: {
            cnpj: companies[cnpj] for cnpj in cnpjs if cnpj in companies
        }
        headers = {"x-organization-id": str(self.other)}
        selection = {"cnpjs": list(companies)}
        status, estimate, _ = self.request("/api/credits/estimate", "POST", selection, self.owner_token, headers)
        self.assertEqual(status, 200)
        self.assertEqual(estimate["credits_required"], 2)
        self.assertTrue(estimate["can_complete"])

        status, csv_content, response_headers = self.request("/api/exports/companies", "POST", selection, self.owner_token, headers)
        self.assertEqual(status, 200)
        self.assertIn("Empresa A", csv_content)
        self.assertEqual(response_headers[b"x-credits-spent"], b"2")
        self.assertEqual(response_headers[b"x-credit-balance"], b"0")

        status, _, response_headers = self.request("/api/exports/companies", "POST", selection, self.owner_token, headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers[b"x-credits-spent"], b"0")
        self.assertEqual(self.request("/api/credits/estimate", "POST", selection, self.owner_token, headers)[1]["credits_required"], 0)

    def test_dashboard_aggregates_only_the_active_organization(self):
        company_list = self.saas.create_company_list(self.other, self.owner["id"], name="Prospects")
        self.saas.create_saved_search(self.other, self.owner["id"], name="SP", filters={"ufs": ["SP"]})
        self.saas.add_companies(self.other, company_list["id"], self.owner["id"], [{"cnpj": "11222333000181", "legal_name": "Empresa A"}])
        self.jobs.create_job("running.csv", [], active_only=True, check_website=False, organization_id=self.other)
        status, dashboard, _ = self.request(
            "/api/dashboard", token=self.owner_token, headers={"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["list_count"], 1)
        self.assertEqual(dashboard["saved_search_count"], 1)
        self.assertEqual(dashboard["unlocked_companies"], 1)
        self.assertEqual(dashboard["active_jobs"], 1)
        self.assertEqual(dashboard["recent_lists"][0]["name"], "Prospects")
        self.assertEqual(dashboard["usage"]["period_days"], 30)
        self.assertEqual(dashboard["usage"]["unlocked_companies"], 1)
        self.assertEqual(dashboard["usage"]["credits_spent"], 1)
        self.assertGreaterEqual(dashboard["usage"]["active_days"], 1)
        self.assertEqual(len(dashboard["usage"]["daily"]), 30)
        self.assertEqual(dashboard["organization_id"], self.other)
        self.assertEqual(dashboard["onboarding"], {"member_count": 1, "pending_invitation_count": 0})

    def test_notifications_are_scoped_and_can_be_marked_read(self):
        job = self.jobs.create_job(
            "concluido.csv", [], active_only=True, check_website=False,
            organization_id=self.other, created_by=self.owner["id"],
        )
        self.jobs.finish_if_complete(job["id"])
        headers = {"x-organization-id": str(self.other)}
        status, data, _ = self.request("/api/notifications", token=self.owner_token, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual({item["kind"] for item in data["notifications"]}, {"job_completed", "low_credit"})
        notification_id = data["notifications"][0]["id"]
        self.assertEqual(
            self.request(
                f"/api/notifications/{notification_id}/read", "POST", token=self.member_token,
                headers={"x-organization-id": str(self.org)},
            )[0],
            404,
        )
        self.assertEqual(
            self.request(
                f"/api/notifications/{notification_id}/read", "POST", token=self.owner_token,
                headers=headers,
            )[0],
            200,
        )
        status, marked, _ = self.request(
            "/api/notifications/read-all", "POST", token=self.owner_token, headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(marked["updated"], 1)
        self.assertEqual(self.request("/api/notifications", token=self.owner_token, headers=headers)[1]["unread_count"], 0)

    def test_support_flow_is_tenant_scoped_and_managed_by_internal_admin(self):
        customer_headers = {"x-organization-id": str(self.other)}
        status, context, _ = self.request("/api/support/context", token=self.owner_token, headers=customer_headers)
        self.assertEqual(status, 200)
        self.assertEqual(context["organization"]["name"], "Other")
        status, ticket, _ = self.request(
            "/api/support/tickets", "POST",
            {
                "category": "technical", "priority": "high",
                "subject": "Exportação não conclui",
                "message": "A exportação continua carregando após selecionar as empresas.",
            },
            self.owner_token, customer_headers,
        )
        self.assertEqual(status, 201)
        self.assertEqual(ticket["organization_id"], self.other)
        ticket_id = ticket["id"]
        self.assertEqual(self.request("/api/support/tickets", token=self.owner_token, headers=customer_headers)[1]["tickets"][0]["id"], ticket_id)
        self.assertEqual(self.request("/api/support/tickets", token=self.owner_token, headers={"x-organization-id": str(self.org)})[1]["tickets"], [])
        self.assertEqual(self.request(f"/api/support/tickets/{ticket_id}", token=self.member_token)[0], 404)

        internal_headers = {"x-organization-id": str(self.org)}
        status, queue, _ = self.request("/api/admin/support/tickets", token=self.owner_token, headers=internal_headers)
        self.assertEqual(status, 200)
        self.assertEqual(queue["tickets"][0]["organization_name"], "Other")
        status, updated, _ = self.request(
            f"/api/admin/support/tickets/{ticket_id}", "PATCH",
            {"status": "in_progress", "priority": "urgent"},
            self.owner_token, internal_headers,
        )
        self.assertEqual((status, updated["priority"]), (200, "urgent"))
        status, answered, _ = self.request(
            f"/api/admin/support/tickets/{ticket_id}/messages", "POST",
            {"message": "Estamos verificando. Envie o horário aproximado da tentativa."},
            self.owner_token, internal_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(answered["messages"][-1]["author_kind"], "support")
        notices = self.request("/api/notifications", token=self.owner_token, headers=customer_headers)[1]["notifications"]
        self.assertIn("support_reply", {item["kind"] for item in notices})
        self.assertEqual(self.request("/api/admin/support/tickets", token=self.member_token)[0], 403)

    def test_suspended_workspace_can_still_open_support_ticket(self):
        self.saas.update_billing_profile(
            self.other, plan_code="growth", subscription_status="suspended", unlimited_credits=False,
        )
        headers = {"x-organization-id": str(self.other)}
        self.assertEqual(self.request("/api/dashboard", token=self.owner_token, headers=headers)[0], 403)
        self.assertEqual(self.request("/api/support/context", token=self.owner_token, headers=headers)[0], 200)
        status, ticket, _ = self.request(
            "/api/support/tickets", "POST",
            {
                "category": "billing", "priority": "normal",
                "subject": "Acesso da empresa suspenso",
                "message": "Precisamos entender como regularizar o acesso ao workspace.",
            },
            self.owner_token, headers,
        )
        self.assertEqual(status, 201)
        self.assertEqual(ticket["status"], "open")

    def test_database_schema_is_internal_only(self):
        self.main.repository.database_schema.return_value = {"relations": []}
        self.assertEqual(
            self.request("/api/explorer/schema", token=self.owner_token, headers={"x-organization-id": str(self.other)})[0],
            403,
        )
        self.assertEqual(
            self.request("/api/explorer/schema", token=self.owner_token, headers={"x-organization-id": str(self.org)})[0],
            200,
        )
