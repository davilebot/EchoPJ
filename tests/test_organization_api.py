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
        self.patches = [patch.object(self.main, "auth_store", self.auth), patch.object(self.main, "job_store", self.jobs), patch.object(self.main, "saas_store", self.saas), patch.object(self.main, "repository", MagicMock()), patch.object(self.main.job_runner, "notify")]
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
        self.assertEqual(self.request("/api/organizations")[0], 401)
        status, data, _ = self.request("/api/organizations", token=self.member_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["organizations"]), 1)
        self.assertFalse(data["can_create"])

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
            status, pending, _ = self.request(
                "/api/auth/signup", "POST",
                {"name": "Nova Empresa", "email": "new@example.com", "password": "secure-password"},
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
        self.assertEqual(self.saas.billing_summary(organization_id)["profile"]["credit_balance"], self.main.settings.saas_trial_credits)
        self.assertEqual(self.request("/api/auth/signup/verify", "POST", {"token": token})[0], 404)

    def test_internal_admin_can_manage_commercial_profiles(self):
        headers = {"x-organization-id": str(self.org)}
        status, overview, _ = self.request(
            "/api/admin/overview", token=self.owner_token, headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(overview["total"], 2)
        self.assertEqual(overview["user_count"], 2)
        self.assertTrue(any(item["id"] == self.other for item in overview["organizations"]))

        status, profile, _ = self.request(
            f"/api/admin/organizations/{self.other}/billing", "PATCH",
            {"plan_code": "growth", "subscription_status": "active", "unlimited_credits": False},
            self.owner_token, headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(profile["plan_code"], "growth")
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
        status, data, headers = self.request("/api/invitations/accept", "POST", {"token": token, "password": "new-password-long"})
        self.assertEqual(status, 200); self.assertEqual(data["organization_id"], self.other)
        self.assertIn(b"set-cookie", headers)
        self.assertEqual(self.request("/api/invitations/accept", "POST", {"token": token, "password": "new-password-long"})[0], 404)

    def test_removed_members_session_immediately_loses_org_access(self):
        self.auth.change_member(self.owner["id"], self.org, self.member["id"])
        self.assertEqual(self.request("/api/jobs", token=self.member_token)[0], 403)
        self.assertEqual(self.request("/api/auth/me", token=self.member_token)[0], 200)

    def test_jobs_created_are_bound_to_actor_and_org(self):
        payload = {"filename": "test.csv", "items": [{"local_id": "1", "name": "Example", "uf": "SP"}], "check_website": False}
        status, job, _ = self.request("/api/jobs", "POST", payload, self.owner_token, {"x-organization-id": str(self.other)})
        self.assertEqual(status, 200)
        self.assertEqual(job["organization_id"], self.other)
        self.assertEqual(job["created_by"], self.owner["id"])
        self.assertEqual(self.request("/api/jobs", "POST", payload, self.member_token, {"x-organization-id": str(self.other)})[0], 403)

    def test_csrf_blocked_and_sensitive_input_not_echoed(self):
        self.assertEqual(self.request("/api/organizations", "POST", {"name": "Bad"}, self.owner_token, {"origin": "https://attacker.example"})[0], 403)
        secret = "do-not-echo-this-secret"
        status, payload, _ = self.request("/api/invitations/accept", "POST", {"token": secret, "password": secret})
        self.assertEqual(status, 422); self.assertNotIn(secret, str(payload))

    def test_admin_pages_and_membership_free_invite_page(self):
        self.assertEqual(self.request("/organizations")[0], 303)
        self.assertEqual(self.request("/organizations", token=self.owner_token)[0], 200)
        self.assertEqual(self.request("/admin")[0], 303)
        self.assertEqual(self.request("/admin", token=self.owner_token)[0], 200)
        status, html, headers = self.request("/invite")
        self.assertEqual(status, 200); self.assertIn('id="accept-form"', html)
        self.assertEqual(headers[b"referrer-policy"], b"no-referrer")
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
        self.assertEqual(
            self.request(f"/api/saved-searches/{saved['id']}", token=self.owner_token, headers={"x-organization-id": str(self.other)})[0],
            404,
        )
        status, company_list, _ = self.request(
            "/api/company-lists", "POST", {"name": "Prospects"}, self.owner_token,
            {"x-organization-id": str(self.other)},
        )
        self.assertEqual(status, 201)
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
        self.assertEqual(dashboard["organization_id"], self.other)
        self.assertEqual(dashboard["onboarding"], {"member_count": 1, "pending_invitation_count": 0})

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
