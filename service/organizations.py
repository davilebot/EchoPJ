"""Organization permissions and single-use invitations, stored alongside accounts."""
import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


class OrganizationError(ValueError):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.status = status


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def invitation_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class OrganizationStoreMixin:
    def _initialize_organizations(self):
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memberships (
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                role TEXT NOT NULL CHECK(role IN ('admin','member')),
                joined_at TEXT NOT NULL,
                PRIMARY KEY(organization_id,user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                email TEXT NOT NULL COLLATE NOCASE,
                role TEXT NOT NULL CHECK(role IN ('admin','member')),
                token_hash TEXT NOT NULL UNIQUE,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                accepted_at TEXT,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_invites_org ON invitations(organization_id);
            CREATE TABLE IF NOT EXISTS organization_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                actor_id INTEGER NOT NULL REFERENCES users(id),
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)

    @contextmanager
    def _org_transaction(self):
        # Serialize read-check-write across connections, including the last-admin rule.
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def _audit(self, org_id, actor_id, action, target=""):
        self._connection.execute(
            "INSERT INTO organization_audit(organization_id,actor_id,action,target,created_at) VALUES(?,?,?,?,?)",
            (org_id, actor_id, action, str(target), now_iso()),
        )

    def ensure_initial_organization(self):
        """Assign legacy access once. Never re-add removed members on restart."""
        with self._org_transaction():
            saved = self._connection.execute("SELECT value FROM auth_metadata WHERE key='legacy_organization'").fetchone()
            if saved:
                return int(saved[0])
            users = self._connection.execute("SELECT id FROM users ORDER BY id").fetchall()
            if not users:
                return None
            owner = users[0]["id"]
            org_id = self._connection.execute(
                "INSERT INTO organizations(name,created_by,created_at) VALUES(?,?,?)",
                ("Minha organização", owner, now_iso()),
            ).lastrowid
            self._connection.executemany(
                "INSERT INTO memberships VALUES(?,?,?,?)",
                [(org_id, u["id"], "admin" if u["id"] == owner else "member", now_iso()) for u in users],
            )
            self._connection.execute("INSERT INTO auth_metadata VALUES('legacy_organization',?)", (str(org_id),))
            self._audit(org_id, owner, "organization.migrated")
            return org_id

    def organizations_for_user(self, user_id):
        with self._lock:
            return [dict(r) for r in self._connection.execute(
                "SELECT o.*,m.role FROM organizations o JOIN memberships m ON m.organization_id=o.id WHERE m.user_id=? ORDER BY o.id",
                (user_id,),
            ).fetchall()]

    def _membership(self, user_id, org_id, *, admin=False):
        row = self._connection.execute(
            "SELECT o.*,m.role FROM organizations o JOIN memberships m ON m.organization_id=o.id WHERE o.id=? AND m.user_id=?",
            (org_id, user_id),
        ).fetchone()
        if not row or (admin and row["role"] != "admin"):
            raise OrganizationError("Você não tem permissão para acessar esta organização.", 403)
        return dict(row)

    def organization_for_user(self, user_id, org_id=None, *, admin=False):
        with self._lock:
            if org_id is None:
                row = self._connection.execute("SELECT organization_id FROM memberships WHERE user_id=? ORDER BY organization_id LIMIT 1", (user_id,)).fetchone()
                if not row:
                    raise OrganizationError("Sua conta não está em uma organização. Peça um convite ao administrador.", 403)
                org_id = row[0]
            return self._membership(user_id, org_id, admin=admin)

    def create_organization(self, user_id, name):
        with self._org_transaction():
            if not self._connection.execute("SELECT 1 FROM memberships WHERE user_id=? AND role='admin'", (user_id,)).fetchone():
                raise OrganizationError("Somente administradores podem criar organizações.", 403)
            org_id = self._connection.execute("INSERT INTO organizations(name,created_by,created_at) VALUES(?,?,?)", (name, user_id, now_iso())).lastrowid
            self._connection.execute("INSERT INTO memberships VALUES(?,?,'admin',?)", (org_id, user_id, now_iso()))
            self._audit(org_id, user_id, "organization.created")
            return self._membership(user_id, org_id)

    def rename_organization(self, actor_id, org_id, name):
        with self._org_transaction():
            self._membership(actor_id, org_id, admin=True)
            self._connection.execute("UPDATE organizations SET name=? WHERE id=?", (name, org_id))
            self._audit(org_id, actor_id, "organization.renamed", name)
            return self._membership(actor_id, org_id)

    def organization_team(self, actor_id, org_id):
        with self._lock:
            org = self._membership(actor_id, org_id, admin=True)
            members = [dict(r) for r in self._connection.execute(
                "SELECT u.id,u.identifier,m.role,m.joined_at FROM memberships m JOIN users u ON u.id=m.user_id WHERE organization_id=? ORDER BY u.id", (org_id,),
            )]
            invites = [dict(r) for r in self._connection.execute(
                """SELECT id,email,role,created_at,expires_at,accepted_at,revoked_at,
                CASE WHEN accepted_at IS NOT NULL THEN 'accepted' WHEN revoked_at IS NOT NULL THEN 'revoked'
                     WHEN expires_at<=? THEN 'expired' ELSE 'pending' END AS status
                FROM invitations WHERE organization_id=? ORDER BY id DESC LIMIT 100""", (now_iso(), org_id),
            )]
            audit = [dict(r) for r in self._connection.execute(
                "SELECT a.action,a.target,a.created_at,u.identifier AS actor FROM organization_audit a JOIN users u ON u.id=a.actor_id WHERE a.organization_id=? ORDER BY a.id DESC LIMIT 50", (org_id,),
            )]
            return {"organization": org, "members": members, "invitations": invites, "audit": audit}

    def change_member(self, actor_id, org_id, user_id, role=None):
        if role not in (None, "admin", "member"):
            raise OrganizationError("Perfil inválido.")
        with self._org_transaction():
            self._membership(actor_id, org_id, admin=True)
            member = self._connection.execute("SELECT role FROM memberships WHERE organization_id=? AND user_id=?", (org_id, user_id)).fetchone()
            if not member:
                raise OrganizationError("Membro não encontrado.", 404)
            if member["role"] == "admin" and role != "admin":
                admins = self._connection.execute("SELECT count(*) FROM memberships WHERE organization_id=? AND role='admin'", (org_id,)).fetchone()[0]
                if admins <= 1:
                    raise OrganizationError("Mantenha pelo menos um administrador. Promova outra pessoa primeiro.", 409)
            if role is None:
                self._connection.execute("DELETE FROM memberships WHERE organization_id=? AND user_id=?", (org_id, user_id))
            else:
                self._connection.execute("UPDATE memberships SET role=? WHERE organization_id=? AND user_id=?", (role, org_id, user_id))
            if role != "admin":
                # A demoted/removed administrator's unused invitations cannot grant access later.
                self._connection.execute("UPDATE invitations SET revoked_at=? WHERE organization_id=? AND created_by=? AND accepted_at IS NULL AND revoked_at IS NULL", (now_iso(), org_id, user_id))
            self._audit(org_id, actor_id, "member.removed" if role is None else "member.role_changed", f"{user_id}:{role or 'removed'}")

    def create_invitation(self, actor_id, org_id, email, role):
        if role not in ("admin", "member"):
            raise OrganizationError("Perfil inválido.")
        email = email.strip().casefold()
        token = secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc)
        expires = (created + timedelta(days=7)).isoformat()
        with self._org_transaction():
            org = self._membership(actor_id, org_id, admin=True)
            if self._connection.execute("SELECT 1 FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.organization_id=? AND u.identifier=? COLLATE NOCASE", (org_id, email)).fetchone():
                raise OrganizationError("Este e-mail já participa da organização.", 409)
            self._connection.execute("UPDATE invitations SET revoked_at=? WHERE organization_id=? AND email=? AND accepted_at IS NULL AND revoked_at IS NULL", (created.isoformat(), org_id, email))
            invite_id = self._connection.execute(
                "INSERT INTO invitations(organization_id,email,role,token_hash,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (org_id, email, role, invitation_hash(token), actor_id, created.isoformat(), expires),
            ).lastrowid
            self._audit(org_id, actor_id, "invitation.created", email)
        return {"id": invite_id, "email": email, "role": role, "organization_name": org["name"], "expires_at": expires, "token": token}

    def revoke_invitation(self, actor_id, org_id, invitation_id):
        with self._org_transaction():
            self._membership(actor_id, org_id, admin=True)
            row = self._connection.execute("SELECT id FROM invitations WHERE id=? AND organization_id=? AND accepted_at IS NULL AND revoked_at IS NULL", (invitation_id, org_id)).fetchone()
            if not row:
                raise OrganizationError("Convite não encontrado ou já encerrado.", 404)
            self._connection.execute("UPDATE invitations SET revoked_at=? WHERE id=?", (now_iso(), invitation_id))
            self._audit(org_id, actor_id, "invitation.revoked", invitation_id)

    def _valid_invitation(self, token):
        row = self._connection.execute(
            """SELECT i.*,o.name AS organization_name FROM invitations i JOIN organizations o ON o.id=i.organization_id
            JOIN memberships m ON m.organization_id=i.organization_id AND m.user_id=i.created_by AND m.role='admin'
            WHERE token_hash=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?""",
            (invitation_hash(token), now_iso()),
        ).fetchone()
        if not row:
            raise OrganizationError("Convite inválido, expirado ou já utilizado. Peça um novo ao administrador.", 404)
        return row

    def invitation_preview(self, token):
        with self._lock:
            row = self._valid_invitation(token)
            return {key: row[key] for key in ("email", "organization_name", "role", "expires_at")}

    def accept_invitation(self, token, password):
        with self._org_transaction():
            row = self._valid_invitation(token)
            existing = self._connection.execute("SELECT * FROM users WHERE identifier=? COLLATE NOCASE", (row["email"],)).fetchone()
            if existing:
                user = self.authenticate(row["email"], password)
                if not user:
                    raise OrganizationError("Este e-mail já tem conta. Use a senha dessa conta para aceitar o convite.", 401)
            else:
                if not 8 <= len(password) <= 1024:
                    raise OrganizationError("Escolha uma senha de pelo menos 8 caracteres.", 422)
                user = self._insert_invited_user(row["email"], password)
            # Existing members never gain an elevated role from an older pending invite.
            self._connection.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?,?)", (row["organization_id"], user["id"], row["role"], now_iso()))
            self._connection.execute("UPDATE invitations SET accepted_at=? WHERE id=?", (now_iso(), row["id"]))
            self._audit(row["organization_id"], user["id"], "invitation.accepted", row["id"])
            return user, row["organization_id"]
