import sqlite3
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from service.auth import AuthStore, LoginRateLimiter
from service.models import AccountUpdateRequest


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


class AuthFrontendTests(unittest.TestCase):
    def test_login_and_account_pages_have_expected_controls(self):
        static_dir = Path(__file__).resolve().parents[1] / "service" / "static"
        login = (static_dir / "login.html").read_text(encoding="utf-8")
        account = (static_dir / "account.html").read_text(encoding="utf-8")
        index = (static_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="login-form"', login)
        self.assertIn('autocomplete="current-password"', login)
        self.assertIn('id="account-form"', account)
        self.assertIn('autocomplete="new-password"', account)
        self.assertIn('id="logout-button"', account)
        self.assertIn('id="account-link"', index)


if __name__ == "__main__":
    unittest.main()
