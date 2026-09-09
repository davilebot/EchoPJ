import sqlite3
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from service.auth import AuthStore, LoginRateLimiter
from service.models import AccountUpdateRequest, PasswordResetConfirmRequest, SignupRequest


class AuthStoreTests(unittest.TestCase):
    def test_migrates_legacy_credential_and_never_stores_plain_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.sqlite"
            store = AuthStore(str(path), session_days=30)
            self.assertTrue(store.bootstrap("Admin", "senha-antiga-segura"))
            self.assertFalse(store.bootstrap("outro", "nao-pode-sobrescrever"))
            self.assertEqual(store.authenticate("admin", "senha-antiga-segura")["identifier"], "admin")
            self.assertIsNone(store.authenticate("admin", "senha-errada"))

            connection = sqlite3.connect(path)
            stored_hash = connection.execute("SELECT password_hash FROM users").fetchone()[0]
            connection.close()
            self.assertNotIn(b"senha-antiga-segura", stored_hash)
            store.close()

    def test_account_change_invalidates_old_password_and_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(str(Path(directory) / "auth.sqlite"), session_days=30)
            store.bootstrap("admin", "senha-antiga-segura")
            user = store.authenticate("admin", "senha-antiga-segura")
            old_token, _ = store.create_session(user["id"])
            self.assertEqual(store.user_for_session(old_token)["identifier"], "admin")

            updated = store.update_account(
                user["id"],
                current_password="senha-antiga-segura",
                identifier="davi@example.com",
                new_password="senha-nova-segura",
            )
            self.assertEqual(updated["identifier"], "davi@example.com")
            self.assertIsNone(store.user_for_session(old_token))
            self.assertIsNone(store.authenticate("admin", "senha-antiga-segura"))
            self.assertIsNotNone(store.authenticate("davi@example.com", "senha-nova-segura"))
            store.close()

    def test_wrong_current_password_does_not_change_account(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(str(Path(directory) / "auth.sqlite"))
            store.bootstrap("admin", "senha-antiga-segura")
            user = store.authenticate("admin", "senha-antiga-segura")
            self.assertIsNone(store.update_account(
                user["id"],
                current_password="incorreta",
                identifier="davi@example.com",
                new_password="senha-nova-segura",
            ))
            self.assertIsNotNone(store.authenticate("admin", "senha-antiga-segura"))
            store.close()

    def test_privacy_export_and_reviewed_deletion_request_never_expose_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(str(Path(directory) / "auth.sqlite"))
            store.bootstrap("owner@example.com", "senha-segura-atual")
            user = store.authenticate("owner@example.com", "senha-segura-atual")
            organization_id = store.ensure_initial_organization()
            store.create_session(user["id"])

            self.assertIsNone(store.create_account_deletion_request(
                user["id"], current_password="senha-incorreta", reason="Teste",
            ))
            created = store.create_account_deletion_request(
                user["id"], current_password="senha-segura-atual", reason="Encerrar acesso",
            )
            self.assertTrue(created["created"])
            duplicate = store.create_account_deletion_request(
                user["id"], current_password="senha-segura-atual", reason="Outro motivo",
            )
            self.assertFalse(duplicate["created"])
            self.assertEqual(duplicate["id"], created["id"])

            exported = store.account_data_export(user["id"])
            self.assertEqual(exported["memberships"][0]["organization_id"], organization_id)
            serialized = str(exported)
            self.assertNotIn("senha-segura-atual", serialized)
            self.assertNotIn("password_hash", serialized)
            self.assertNotIn("token_hash", serialized)

            queue = store.admin_privacy_requests(status="requested")
            self.assertEqual(queue[0]["identifier"], "owner@example.com")
            reviewed = store.update_privacy_request(
                created["id"], status="in_review", resolution_note="Validando workspace", handled_by=user["id"],
            )
            self.assertEqual(reviewed["status"], "in_review")
            self.assertEqual(store.cancel_account_deletion_request(user["id"])["status"], "canceled")
            store.close()

    def test_account_form_requires_email_and_eight_character_password(self):
        with self.assertRaises(ValidationError):
            AccountUpdateRequest(
                identifier="admin",
                current_password="atual",
                new_password="curta",
            )

    def test_rate_limiter_unlocks_after_success(self):
        limiter = LoginRateLimiter(attempts=2, window_seconds=60)
        self.assertTrue(limiter.allowed("client", 10))
        limiter.failed("client", 10)
        limiter.failed("client", 11)
        self.assertFalse(limiter.allowed("client", 12))
        limiter.succeeded("client")
        self.assertTrue(limiter.allowed("client", 13))

    def test_password_reset_is_single_use_and_invalidates_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.sqlite"
            store = AuthStore(str(path), session_days=30)
            store.bootstrap("owner@example.com", "senha-antiga-segura")
            user = store.authenticate("owner@example.com", "senha-antiga-segura")
            old_session, _ = store.create_session(user["id"])
            reset = store.create_password_reset("OWNER@example.com", valid_minutes=30)
            self.assertEqual(reset["identifier"], "owner@example.com")

            connection = sqlite3.connect(path)
            stored = connection.execute("SELECT token_hash FROM password_reset_tokens").fetchone()[0]
            connection.close()
            self.assertNotEqual(stored, reset["token"])

            updated = store.reset_password(reset["token"], "senha-nova-segura")
            self.assertEqual(updated["identifier"], "owner@example.com")
            self.assertIsNone(store.reset_password(reset["token"], "outra-senha-segura"))
            self.assertIsNone(store.user_for_session(old_session))
            self.assertIsNone(store.authenticate("owner@example.com", "senha-antiga-segura"))
            self.assertIsNotNone(store.authenticate("owner@example.com", "senha-nova-segura"))
            store.close()

    def test_new_password_reset_replaces_previous_link_without_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(str(Path(directory) / "auth.sqlite"))
            store.bootstrap("owner@example.com", "senha-antiga-segura")
            first = store.create_password_reset("owner@example.com")
            second = store.create_password_reset("owner@example.com")
            self.assertIsNone(store.create_password_reset("missing@example.com"))
            self.assertIsNone(store.reset_password(first["token"], "senha-nova-segura"))
            self.assertIsNotNone(store.reset_password(second["token"], "senha-nova-segura"))
            store.close()

    def test_password_reset_model_requires_a_stronger_minimum(self):
        with self.assertRaises(ValidationError):
            PasswordResetConfirmRequest(token="x" * 40, new_password="curta")

    def test_verified_signup_creates_owner_and_organization_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.sqlite"
            store = AuthStore(str(path))
            pending = store.create_signup(
                "NEW@Example.com", "secure-password", "  Empresa   Nova  ", valid_hours=24,
                legal_versions={"terms": "2026-09", "privacy": "2026-09"},
            )
            connection = sqlite3.connect(path)
            stored = connection.execute(
                "SELECT token_hash,password_hash,organization_name FROM pending_signups"
            ).fetchone()
            connection.close()
            self.assertNotEqual(stored[0], pending["token"])
            self.assertNotIn(b"secure-password", stored[1])
            self.assertEqual(stored[2], "Empresa Nova")

            user, organization_id = store.complete_signup(pending["token"])
            self.assertEqual(user["identifier"], "new@example.com")
            self.assertEqual(store.organization_for_user(user["id"], organization_id)["role"], "admin")
            self.assertIsNotNone(store.authenticate("new@example.com", "secure-password"))
            acceptances = store.legal_acceptances(user["id"])
            self.assertEqual({item["document_type"] for item in acceptances}, {"terms", "privacy"})
            self.assertTrue(all(item["document_version"] == "2026-09" for item in acceptances))
            self.assertEqual(len(store.account_data_export(user["id"])["legal_acceptances"]), 2)
            self.assertIsNone(store.complete_signup(pending["token"]))
            store.close()

    def test_signup_model_validates_email_password_and_company(self):
        common = {
            "name": "Empresa", "email": "OWNER@Example.com", "password": "secure-password",
            "accept_terms": True, "terms_version": "2026-09", "privacy_version": "2026-09",
        }
        valid = SignupRequest(**common)
        self.assertEqual(valid.email, "owner@example.com")
        for payload in (
            {**common, "name": ""},
            {**common, "email": "invalid"},
            {**common, "password": "short"},
            {**common, "accept_terms": False},
            {**common, "terms_version": "versão inválida"},
            {key: value for key, value in common.items() if key != "privacy_version"},
        ):
            with self.assertRaises(ValidationError):
                SignupRequest(**payload)


class AuthFrontendTests(unittest.TestCase):
    def test_login_and_account_pages_have_expected_controls(self):
        static_dir = Path(__file__).resolve().parents[1] / "service" / "static"
        login = (static_dir / "login.html").read_text(encoding="utf-8")
        account = (static_dir / "account.html").read_text(encoding="utf-8")
        index = (static_dir / "index.html").read_text(encoding="utf-8")
        forgot = (static_dir / "forgot-password.html").read_text(encoding="utf-8")
        reset = (static_dir / "reset-password.html").read_text(encoding="utf-8")
        signup = (static_dir / "signup.html").read_text(encoding="utf-8")
        verify = (static_dir / "verify-email.html").read_text(encoding="utf-8")
        admin = (static_dir / "admin.html").read_text(encoding="utf-8")
        self.assertIn('id="login-form"', login)
        self.assertIn('autocomplete="current-password"', login)
        self.assertIn('href="/forgot-password"', login)
        self.assertIn('id="forgot-form"', forgot)
        self.assertIn('autocomplete="email"', forgot)
        self.assertIn('id="reset-form"', reset)
        self.assertEqual(reset.count('autocomplete="new-password"'), 2)
        self.assertIn('id="signup-form"', signup)
        self.assertEqual(signup.count('autocomplete="new-password"'), 2)
        self.assertIn('id="signup-legal-consent"', signup)
        self.assertIn('href="/termos"', signup)
        self.assertIn('href="/privacidade"', signup)
        self.assertIn('id="verify-message"', verify)
        self.assertIn('id="admin-organizations"', admin)
        self.assertIn('id="admin-billing-form"', admin)
        self.assertIn('id="admin-credit-form"', admin)
        self.assertIn('id="account-form"', account)
        self.assertIn('autocomplete="new-password"', account)
        self.assertIn('id="logout-button"', account)
        self.assertIn('id="account-link"', index)


if __name__ == "__main__":
    unittest.main()
