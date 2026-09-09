import hashlib
import hmac
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .organizations import OrganizationError, OrganizationStoreMixin


PASSWORD_ITERATIONS = 600_000
ACTIVE_PRIVACY_REQUEST_STATUSES = ("requested", "in_review", "waiting_user", "approved")
PRIVACY_REQUEST_STATUSES = (*ACTIVE_PRIVACY_REQUEST_STATUSES, "canceled", "closed")


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
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              used_at TEXT,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_password_resets_user
              ON password_reset_tokens(user_id, expires_at);
            CREATE TABLE IF NOT EXISTS pending_signups (
              token_hash TEXT PRIMARY KEY,
              identifier TEXT NOT NULL COLLATE NOCASE,
              organization_name TEXT NOT NULL,
              password_hash BLOB NOT NULL,
              password_salt BLOB NOT NULL,
              password_iterations INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pending_signups_email
              ON pending_signups(identifier, expires_at);
            CREATE TABLE IF NOT EXISTS account_deletion_requests (
              id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('requested','in_review','waiting_user','approved','canceled','closed')),
              reason TEXT NOT NULL DEFAULT '',
              resolution_note TEXT NOT NULL DEFAULT '',
              requested_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              handled_by INTEGER,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
              FOREIGN KEY(handled_by) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account_deletion_status
              ON account_deletion_requests(status, requested_at);
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

    def health_check(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1").fetchone()[0] == 1

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

    def create_password_reset(self, identifier: str, *, valid_minutes: int = 30) -> dict[str, Any] | None:
        normalized = normalize_identifier(identifier)
        created_at = utc_now()
        with self._lock, self._connection:
            user = self._connection.execute(
                "SELECT id,identifier FROM users WHERE identifier=? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
            self._connection.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at<=?",
                (isoformat(created_at),),
            )
            if not user or "@" not in user["identifier"]:
                return None
            token = secrets.token_urlsafe(32)
            expires_at = created_at + timedelta(minutes=max(5, valid_minutes))
            self._connection.execute(
                "UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (isoformat(created_at), user["id"]),
            )
            self._connection.execute(
                """INSERT INTO password_reset_tokens(token_hash,user_id,created_at,expires_at)
                   VALUES(?,?,?,?)""",
                (token_digest(token), user["id"], isoformat(created_at), isoformat(expires_at)),
            )
        return {"token": token, "identifier": user["identifier"], "expires_at": isoformat(expires_at)}

    def reset_password(self, token: str, new_password: str) -> dict[str, Any] | None:
        if not 8 <= len(new_password) <= 1024:
            raise ValueError("Escolha uma senha de pelo menos 8 caracteres.")
        changed_at = utc_now()
        with self._org_transaction():
            reset = self._connection.execute(
                """SELECT r.user_id,u.* FROM password_reset_tokens r
                   JOIN users u ON u.id=r.user_id
                   WHERE r.token_hash=? AND r.used_at IS NULL AND r.expires_at>?""",
                (token_digest(token), isoformat(changed_at)),
            ).fetchone()
            if not reset:
                return None
            salt = secrets.token_bytes(16)
            self._connection.execute(
                """UPDATE users SET password_hash=?,password_salt=?,password_iterations=?,updated_at=?
                   WHERE id=?""",
                (
                    password_digest(new_password, salt), salt, PASSWORD_ITERATIONS,
                    isoformat(changed_at), reset["user_id"],
                ),
            )
            self._connection.execute(
                "UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (isoformat(changed_at), reset["user_id"]),
            )
            self._connection.execute("DELETE FROM sessions WHERE user_id=?", (reset["user_id"],))
            updated = self._connection.execute("SELECT * FROM users WHERE id=?", (reset["user_id"],)).fetchone()
        return self._public_user(updated)

    def create_signup(
        self,
        identifier: str,
        password: str,
        organization_name: str,
        *,
        valid_hours: int = 24,
    ) -> dict[str, Any]:
        normalized = normalize_identifier(identifier)
        if "@" not in normalized:
            raise OrganizationError("Informe um e-mail válido.", 422)
        if not 8 <= len(password) <= 1024:
            raise OrganizationError("Escolha uma senha de pelo menos 8 caracteres.", 422)
        organization_name = " ".join(organization_name.split())
        if not organization_name:
            raise OrganizationError("Informe o nome da empresa.", 422)
        created_at = utc_now()
        token = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        with self._org_transaction():
            if self._connection.execute(
                "SELECT 1 FROM users WHERE identifier=? COLLATE NOCASE",
                (normalized,),
            ).fetchone():
                raise OrganizationError("Este e-mail já possui uma conta. Entre ou recupere sua senha.", 409)
            self._connection.execute(
                "DELETE FROM pending_signups WHERE expires_at<=?",
                (isoformat(created_at),),
            )
            self._connection.execute(
                "UPDATE pending_signups SET used_at=? WHERE identifier=? COLLATE NOCASE AND used_at IS NULL",
                (isoformat(created_at), normalized),
            )
            expires_at = created_at + timedelta(hours=max(1, valid_hours))
            self._connection.execute(
                """INSERT INTO pending_signups(
                     token_hash,identifier,organization_name,password_hash,password_salt,
                     password_iterations,created_at,expires_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    token_digest(token), normalized, organization_name,
                    password_digest(password, salt), salt, PASSWORD_ITERATIONS,
                    isoformat(created_at), isoformat(expires_at),
                ),
            )
        return {"token": token, "identifier": normalized, "expires_at": isoformat(expires_at)}

    def complete_signup(self, token: str) -> tuple[dict[str, Any], int] | None:
        completed_at = utc_now()
        with self._org_transaction():
            signup = self._connection.execute(
                """SELECT * FROM pending_signups
                   WHERE token_hash=? AND used_at IS NULL AND expires_at>?""",
                (token_digest(token), isoformat(completed_at)),
            ).fetchone()
            if not signup:
                return None
            if self._connection.execute(
                "SELECT 1 FROM users WHERE identifier=? COLLATE NOCASE",
                (signup["identifier"],),
            ).fetchone():
                raise OrganizationError("Este e-mail já possui uma conta. Entre para continuar.", 409)
            user_id = self._connection.execute(
                """INSERT INTO users(
                     identifier,password_hash,password_salt,password_iterations,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    signup["identifier"], signup["password_hash"], signup["password_salt"],
                    signup["password_iterations"], isoformat(completed_at), isoformat(completed_at),
                ),
            ).lastrowid
            organization_id = self._connection.execute(
                "INSERT INTO organizations(name,created_by,created_at) VALUES(?,?,?)",
                (signup["organization_name"], user_id, isoformat(completed_at)),
            ).lastrowid
            self._connection.execute(
                "INSERT INTO memberships VALUES(?,?,'admin',?)",
                (organization_id, user_id, isoformat(completed_at)),
            )
            self._connection.execute(
                "UPDATE pending_signups SET used_at=? WHERE identifier=? COLLATE NOCASE AND used_at IS NULL",
                (isoformat(completed_at), signup["identifier"]),
            )
            self._audit(organization_id, user_id, "organization.signup")
            user = self._connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(user), organization_id

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

    @staticmethod
    def _privacy_request(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "reason": row["reason"],
            "resolution_note": row["resolution_note"],
            "requested_at": row["requested_at"],
            "updated_at": row["updated_at"],
        }

    def verify_password(self, user_id: int, password: str) -> bool:
        with self._lock:
            current = self._connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not current:
            password_digest(password, b"\0" * 16)
            return False
        actual = password_digest(password, current["password_salt"], current["password_iterations"])
        return hmac.compare_digest(actual, current["password_hash"])

    def privacy_summary(self, user_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM account_deletion_requests WHERE user_id=? ORDER BY requested_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return {"deletion_request": self._privacy_request(row)}

    def create_account_deletion_request(
        self, user_id: int, *, current_password: str, reason: str = ""
    ) -> dict[str, Any] | None:
        if not self.verify_password(user_id, current_password):
            return None
        now = isoformat(utc_now())
        placeholders = ",".join("?" for _ in ACTIVE_PRIVACY_REQUEST_STATUSES)
        with self._lock, self._connection:
            existing = self._connection.execute(
                f"SELECT * FROM account_deletion_requests WHERE user_id=? AND status IN ({placeholders}) ORDER BY requested_at DESC LIMIT 1",
                (user_id, *ACTIVE_PRIVACY_REQUEST_STATUSES),
            ).fetchone()
            if existing:
                result = self._privacy_request(existing)
                result["created"] = False
                return result
            request_id = str(uuid.uuid4())
            self._connection.execute(
                """INSERT INTO account_deletion_requests(
                     id,user_id,status,reason,requested_at,updated_at
                   ) VALUES(?,?,'requested',?,?,?)""",
                (request_id, user_id, reason.strip(), now, now),
            )
            row = self._connection.execute(
                "SELECT * FROM account_deletion_requests WHERE id=?", (request_id,)
            ).fetchone()
        result = self._privacy_request(row)
        result["created"] = True
        return result

    def cancel_account_deletion_request(self, user_id: int) -> dict[str, Any] | None:
        now = isoformat(utc_now())
        placeholders = ",".join("?" for _ in ACTIVE_PRIVACY_REQUEST_STATUSES)
        with self._lock, self._connection:
            row = self._connection.execute(
                f"SELECT * FROM account_deletion_requests WHERE user_id=? AND status IN ({placeholders}) ORDER BY requested_at DESC LIMIT 1",
                (user_id, *ACTIVE_PRIVACY_REQUEST_STATUSES),
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                "UPDATE account_deletion_requests SET status='canceled',updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            updated = self._connection.execute(
                "SELECT * FROM account_deletion_requests WHERE id=?", (row["id"],)
            ).fetchone()
        return self._privacy_request(updated)

    def account_data_export(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            account = self._connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not account:
                return None
            memberships = self._connection.execute(
                """SELECT o.id AS organization_id,o.name,m.role,m.joined_at,o.created_at AS organization_created_at
                   FROM memberships m JOIN organizations o ON o.id=m.organization_id
                   WHERE m.user_id=? ORDER BY o.id""",
                (user_id,),
            ).fetchall()
            sessions = self._connection.execute(
                "SELECT created_at,expires_at,last_seen_at FROM sessions WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            audit = self._connection.execute(
                "SELECT organization_id,action,created_at FROM organization_audit WHERE actor_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            requests = self._connection.execute(
                """SELECT id,status,reason,resolution_note,requested_at,updated_at
                   FROM account_deletion_requests WHERE user_id=? ORDER BY requested_at DESC""",
                (user_id,),
            ).fetchall()
        return {
            "account": self._public_user(account),
            "memberships": [dict(row) for row in memberships],
            "sessions": [dict(row) for row in sessions],
            "organization_actions": [dict(row) for row in audit],
            "deletion_requests": [dict(row) for row in requests],
        }

    def admin_privacy_requests(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if status and status not in PRIVACY_REQUEST_STATUSES:
            raise ValueError("Status de privacidade inválido.")
        query = """SELECT r.*,u.identifier,
                   COALESCE(group_concat(DISTINCT o.name),'Sem workspace') AS organizations
                   FROM account_deletion_requests r
                   JOIN users u ON u.id=r.user_id
                   LEFT JOIN memberships m ON m.user_id=u.id
                   LEFT JOIN organizations o ON o.id=m.organization_id"""
        parameters: list[Any] = []
        if status:
            query += " WHERE r.status=?"
            parameters.append(status)
        query += " GROUP BY r.id ORDER BY r.requested_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [
            {**self._privacy_request(row), "identifier": row["identifier"], "organizations": row["organizations"]}
            for row in rows
        ]

    def update_privacy_request(
        self, request_id: str, *, status: str, resolution_note: str, handled_by: int
    ) -> dict[str, Any] | None:
        if status not in PRIVACY_REQUEST_STATUSES:
            raise ValueError("Status de privacidade inválido.")
        now = isoformat(utc_now())
        with self._lock, self._connection:
            current = self._connection.execute(
                "SELECT 1 FROM account_deletion_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not current:
                return None
            self._connection.execute(
                """UPDATE account_deletion_requests
                   SET status=?,resolution_note=?,handled_by=?,updated_at=? WHERE id=?""",
                (status, resolution_note.strip(), handled_by, now, request_id),
            )
            row = self._connection.execute(
                """SELECT r.*,u.identifier,
                   COALESCE(group_concat(DISTINCT o.name),'Sem workspace') AS organizations
                   FROM account_deletion_requests r JOIN users u ON u.id=r.user_id
                   LEFT JOIN memberships m ON m.user_id=u.id
                   LEFT JOIN organizations o ON o.id=m.organization_id
                   WHERE r.id=? GROUP BY r.id""",
                (request_id,),
            ).fetchone()
        return {**self._privacy_request(row), "identifier": row["identifier"], "organizations": row["organizations"]}


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
