import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .organizations import OrganizationStoreMixin


PASSWORD_ITERATIONS = 600_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat()


def normalize_identifier(value: str) -> str:
    return value.strip().casefold()


def password_digest(password: str, salt: bytes, iterations: int = PASSWORD_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore(OrganizationStoreMixin):
    def __init__(self, database_path: str, *, session_days: int = 30):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_days = session_days
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              identifier TEXT NOT NULL UNIQUE COLLATE NOCASE,
              password_hash BLOB NOT NULL,
              password_salt BLOB NOT NULL,
              password_iterations INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expiration ON sessions(expires_at);
        """)
        self._connection.commit()
        self._initialize_organizations()

    def _insert_invited_user(self, identifier: str, password: str) -> dict:
        salt = secrets.token_bytes(16)
        created = isoformat(utc_now())
        user_id = self._connection.execute(
            "INSERT INTO users(identifier,password_hash,password_salt,password_iterations,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (identifier, password_digest(password, salt), salt, PASSWORD_ITERATIONS, created, created),
        ).lastrowid
        return self._public_user(self._connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def bootstrap(self, identifier: str, password: str) -> bool:
        """Import the legacy credential once, without ever overwriting a changed account."""
        if not identifier or not password:
            return False
        with self._lock, self._connection:
            existing = self._connection.execute("SELECT id FROM users LIMIT 1").fetchone()
            if existing:
                return False
            salt = secrets.token_bytes(16)
            created_at = isoformat(utc_now())
            self._connection.execute(
                """INSERT INTO users(
                     identifier,password_hash,password_salt,password_iterations,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    normalize_identifier(identifier),
                    password_digest(password, salt),
                    salt,
                    PASSWORD_ITERATIONS,
                    created_at,
                    created_at,
                ),
            )
            return True

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "identifier": row["identifier"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def authenticate(self, identifier: str, password: str) -> dict[str, Any] | None:
        normalized = normalize_identifier(identifier)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE identifier=? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
        if not row:
            # Perform equivalent work so an unknown login does not return noticeably faster.
            password_digest(password, b"\0" * 16)
            return None
        actual = password_digest(password, row["password_salt"], row["password_iterations"])
        if not hmac.compare_digest(actual, row["password_hash"]):
            return None
        return self._public_user(row)

    def create_session(self, user_id: int) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        created_at = utc_now()
        expires_at = created_at + timedelta(days=self.session_days)
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM sessions WHERE expires_at<=?",
                (isoformat(created_at),),
            )
            self._connection.execute(
                """INSERT INTO sessions(token_hash,user_id,created_at,expires_at,last_seen_at)
                   VALUES(?,?,?,?,?)""",
                (
                    token_digest(token),
                    user_id,
                    isoformat(created_at),
                    isoformat(expires_at),
                    isoformat(created_at),
                ),
            )
        return token, expires_at

    def user_for_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        now = utc_now()
        digest = token_digest(token)
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT users.*
                   FROM sessions
                   JOIN users ON users.id=sessions.user_id
                   WHERE sessions.token_hash=? AND sessions.expires_at>?""",
                (digest, isoformat(now)),
            ).fetchone()
            if row:
                self._connection.execute(
                    "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
                    (isoformat(now), digest),
                )
        return self._public_user(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (token_digest(token),),
            )

    def update_account(
        self,
        user_id: int,
        *,
        current_password: str,
        identifier: str,
        new_password: str | None,
    ) -> dict[str, Any] | None:
        with self._lock:
            current = self._connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not current:
            return None
        actual = password_digest(
            current_password,
            current["password_salt"],
            current["password_iterations"],
        )
        if not hmac.compare_digest(actual, current["password_hash"]):
            return None

        normalized = normalize_identifier(identifier)
        salt = secrets.token_bytes(16) if new_password else current["password_salt"]
        digest = (
            password_digest(new_password, salt)
            if new_password
            else current["password_hash"]
        )
        updated_at = isoformat(utc_now())
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE users
                   SET identifier=?,password_hash=?,password_salt=?,password_iterations=?,updated_at=?
                   WHERE id=?""",
                (normalized, digest, salt, PASSWORD_ITERATIONS, updated_at, user_id),
            )
            # Credential changes invalidate every existing login, including the current one.
            self._connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            updated = self._connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(updated)


class LoginRateLimiter:
    def __init__(self, *, attempts: int = 8, window_seconds: int = 15 * 60):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        return [value for value in self._failures.get(key, []) if value > cutoff]

    def allowed(self, key: str, now: float) -> bool:
        with self._lock:
            recent = self._recent(key, now)
            self._failures[key] = recent
            return len(recent) < self.attempts

    def failed(self, key: str, now: float) -> None:
        with self._lock:
            recent = self._recent(key, now)
            recent.append(now)
            self._failures[key] = recent

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
