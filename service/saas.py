"""Operational SaaS data isolated from the shared Receita database."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SaaSError(ValueError):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.status = status


class SaaSStore:
    """Lists, saved searches and billing state scoped by organization."""

    def __init__(self, database_path: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS organization_profiles (
                organization_id INTEGER PRIMARY KEY,
                plan_code TEXT NOT NULL,
                subscription_status TEXT NOT NULL,
                unlimited_credits INTEGER NOT NULL DEFAULT 0,
                credit_balance INTEGER NOT NULL DEFAULT 0 CHECK(credit_balance >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS credit_ledger (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
                kind TEXT NOT NULL,
                description TEXT NOT NULL,
                reference_id TEXT,
                idempotency_key TEXT UNIQUE,
                actor_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_credit_ledger_org_created
                ON credit_ledger(organization_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS company_unlocks (
                organization_id INTEGER NOT NULL,
                cnpj TEXT NOT NULL,
                unlocked_by INTEGER,
                unlocked_at TEXT NOT NULL,
                PRIMARY KEY(organization_id, cnpj)
            );
            CREATE TABLE IF NOT EXISTS saved_searches (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                name TEXT NOT NULL,
                filters_json TEXT NOT NULL,
                last_result_count INTEGER,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_saved_searches_org_updated
                ON saved_searches(organization_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS company_lists (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_company_lists_org_updated
                ON company_lists(organization_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS company_list_items (
                list_id TEXT NOT NULL REFERENCES company_lists(id) ON DELETE CASCADE,
                organization_id INTEGER NOT NULL,
                cnpj TEXT NOT NULL,
                company_json TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY(list_id, cnpj)
            );
            CREATE INDEX IF NOT EXISTS idx_company_list_items_org_added
                ON company_list_items(organization_id, added_at DESC);
        """)
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    @staticmethod
    def _profile(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["unlimited_credits"] = bool(result["unlimited_credits"])
        result["is_internal"] = result["plan_code"] == "internal"
        result["credit_label"] = "Ilimitados" if result["unlimited_credits"] else f"{result['credit_balance']:,}".replace(",", ".")
        return result

    def ensure_organization(
        self,
        organization_id: int,
        *,
        unlimited: bool = False,
        initial_credits: int = 100,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not row:
                plan_code = "internal" if unlimited else "trial"
                balance = 0 if unlimited else max(0, initial_credits)
                self._connection.execute(
                    """INSERT INTO organization_profiles(
                         organization_id,plan_code,subscription_status,unlimited_credits,
                         credit_balance,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (organization_id, plan_code, "active", int(unlimited), balance, now, now),
                )
                if balance:
                    self._insert_ledger(
                        organization_id,
                        delta=balance,
                        balance_after=balance,
                        kind="trial_grant",
                        description="Créditos de boas-vindas",
                        idempotency_key=f"trial:{organization_id}",
                    )
            elif unlimited and not row["unlimited_credits"]:
                self._connection.execute(
                    """UPDATE organization_profiles
                       SET plan_code='internal',subscription_status='active',
                           unlimited_credits=1,updated_at=?
                       WHERE organization_id=?""",
                    (now, organization_id),
                )
            row = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
        return self._profile(row)

    def _insert_ledger(
        self,
        organization_id: int,
        *,
        delta: int,
        balance_after: int,
        kind: str,
        description: str,
        reference_id: str | None = None,
        idempotency_key: str | None = None,
        actor_id: int | None = None,
    ) -> None:
        self._connection.execute(
            """INSERT INTO credit_ledger(
                 id,organization_id,delta,balance_after,kind,description,
                 reference_id,idempotency_key,actor_id,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), organization_id, delta, balance_after, kind,
                description, reference_id, idempotency_key, actor_id, utc_now(),
            ),
        )

    def _unlocked_cnpjs(self, organization_id: int, cnpjs: list[str]) -> set[str]:
        unlocked: set[str] = set()
        for start in range(0, len(cnpjs), 500):
            chunk = cnpjs[start:start + 500]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                f"SELECT cnpj FROM company_unlocks WHERE organization_id=? AND cnpj IN ({placeholders})",
                (organization_id, *chunk),
            ).fetchall()
            unlocked.update(row[0] for row in rows)
        return unlocked

    def billing_summary(self, organization_id: int, *, ledger_limit: int = 20) -> dict[str, Any]:
        with self._lock:
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Configuração comercial da organização não encontrada.", 404)
            ledger = [dict(row) for row in self._connection.execute(
                """SELECT id,delta,balance_after,kind,description,reference_id,created_at
                   FROM credit_ledger WHERE organization_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (organization_id, ledger_limit),
            ).fetchall()]
            unlocked = self._connection.execute(
                "SELECT count(*) FROM company_unlocks WHERE organization_id=?",
                (organization_id,),
            ).fetchone()[0]
        return {"profile": self._profile(profile), "ledger": ledger, "unlocked_companies": unlocked}

    def credit_estimate(self, organization_id: int, cnpjs: list[str]) -> dict[str, Any]:
        unique = list(dict.fromkeys(cnpjs))
        with self._lock:
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Configuração comercial da organização não encontrada.", 404)
            unlocked = self._unlocked_cnpjs(organization_id, unique)
        required = 0 if profile["unlimited_credits"] else sum(cnpj not in unlocked for cnpj in unique)
        return {
            "requested_companies": len(unique),
            "already_unlocked": sum(cnpj in unlocked for cnpj in unique),
            "credits_required": required,
            "credit_balance": profile["credit_balance"],
            "unlimited_credits": bool(profile["unlimited_credits"]),
            "can_complete": bool(profile["unlimited_credits"] or required <= profile["credit_balance"]),
        }

    def unlock_companies(
        self,
        organization_id: int,
        actor_id: int,
        cnpjs: list[str],
        *,
        kind: str,
        description: str,
        reference_id: str | None = None,
    ) -> dict[str, Any]:
        unique = list(dict.fromkeys(cnpjs))
        now = utc_now()
        with self._transaction():
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Configuração comercial da organização não encontrada.", 404)
            unlocked = self._unlocked_cnpjs(organization_id, unique)
            new_unlocks = [cnpj for cnpj in unique if cnpj not in unlocked]
            credits_spent = 0 if profile["unlimited_credits"] else len(new_unlocks)
            if credits_spent > profile["credit_balance"]:
                raise SaaSError(
                    f"Créditos insuficientes. Esta ação precisa de {credits_spent} e o saldo é {profile['credit_balance']}.",
                    402,
                )
            balance = profile["credit_balance"] - credits_spent
            if credits_spent:
                self._connection.execute(
                    "UPDATE organization_profiles SET credit_balance=?,updated_at=? WHERE organization_id=?",
                    (balance, now, organization_id),
                )
                self._insert_ledger(
                    organization_id,
                    delta=-credits_spent,
                    balance_after=balance,
                    kind=kind,
                    description=description,
                    reference_id=reference_id,
                    actor_id=actor_id,
                )
            self._connection.executemany(
                "INSERT INTO company_unlocks(organization_id,cnpj,unlocked_by,unlocked_at) VALUES(?,?,?,?)",
                [(organization_id, cnpj, actor_id, now) for cnpj in new_unlocks],
            )
        return {
            "unlocked": len(new_unlocks),
            "credits_spent": credits_spent,
            "credit_balance": balance,
            "unlimited_credits": bool(profile["unlimited_credits"]),
        }

    def dashboard_summary(self, organization_id: int) -> dict[str, Any]:
        billing = self.billing_summary(organization_id, ledger_limit=5)
        with self._lock:
            list_count = self._connection.execute(
                "SELECT count(*) FROM company_lists WHERE organization_id=?",
                (organization_id,),
            ).fetchone()[0]
            saved_search_count = self._connection.execute(
                "SELECT count(*) FROM saved_searches WHERE organization_id=?",
                (organization_id,),
            ).fetchone()[0]
            recent_lists = [dict(row) for row in self._connection.execute(
                """SELECT l.id,l.name,l.updated_at,count(i.cnpj) AS company_count
                   FROM company_lists l LEFT JOIN company_list_items i ON i.list_id=l.id
                   WHERE l.organization_id=? GROUP BY l.id
                   ORDER BY l.updated_at DESC LIMIT 3""",
                (organization_id,),
            ).fetchall()]
            recent_searches = [self._saved_search(row) for row in self._connection.execute(
                "SELECT * FROM saved_searches WHERE organization_id=? ORDER BY updated_at DESC LIMIT 3",
                (organization_id,),
            ).fetchall()]
        return {
            **billing,
            "list_count": list_count,
            "saved_search_count": saved_search_count,
            "recent_lists": recent_lists,
            "recent_searches": recent_searches,
        }

    def grant_credits(
        self,
        organization_id: int,
        amount: int,
        *,
        description: str,
        idempotency_key: str,
        reference_id: str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise SaaSError("A quantidade de créditos precisa ser positiva.", 422)
        with self._transaction():
            duplicate = self._connection.execute(
                "SELECT organization_id FROM credit_ledger WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if duplicate:
                if duplicate["organization_id"] != organization_id:
                    raise SaaSError("Chave de pagamento já utilizada por outra organização.", 409)
                row = self._connection.execute(
                    "SELECT * FROM organization_profiles WHERE organization_id=?",
                    (organization_id,),
                ).fetchone()
                return self._profile(row)
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Organização não encontrada.", 404)
            balance = profile["credit_balance"] + amount
            self._connection.execute(
                "UPDATE organization_profiles SET credit_balance=?,updated_at=? WHERE organization_id=?",
                (balance, utc_now(), organization_id),
            )
            self._insert_ledger(
                organization_id,
                delta=amount,
                balance_after=balance,
                kind="credit_grant",
                description=description,
                reference_id=reference_id,
                idempotency_key=idempotency_key,
            )
            row = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
        return self._profile(row)

    def admin_metrics(self) -> dict[str, int]:
        with self._lock:
            row = self._connection.execute(
                """SELECT count(*) AS configured_organizations,
                   sum(CASE WHEN subscription_status IN ('active','trialing') THEN 1 ELSE 0 END) AS active_organizations,
                   coalesce(sum(CASE WHEN unlimited_credits=0 THEN credit_balance ELSE 0 END),0) AS credits_available
                   FROM organization_profiles"""
            ).fetchone()
            unlocked = self._connection.execute(
                "SELECT count(*) FROM company_unlocks"
            ).fetchone()[0]
        return {
            "configured_organizations": row["configured_organizations"],
            "active_organizations": row["active_organizations"] or 0,
            "credits_available": row["credits_available"],
            "unlocked_companies": unlocked,
        }

    def update_billing_profile(
        self,
        organization_id: int,
        *,
        plan_code: str,
        subscription_status: str,
        unlimited_credits: bool,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            current = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not current:
                raise SaaSError("Organização não encontrada.", 404)
            changed = (
                current["plan_code"] != plan_code
                or current["subscription_status"] != subscription_status
                or bool(current["unlimited_credits"]) != bool(unlimited_credits)
            )
            self._connection.execute(
                """UPDATE organization_profiles
                   SET plan_code=?,subscription_status=?,unlimited_credits=?,updated_at=?
                   WHERE organization_id=?""",
                (plan_code, subscription_status, int(unlimited_credits), now, organization_id),
            )
            if changed:
                self._insert_ledger(
                    organization_id,
                    delta=0,
                    balance_after=current["credit_balance"],
                    kind="billing_profile_update",
                    description=f"Plano {plan_code} · status {subscription_status}",
                    idempotency_key=f"billing-admin:{uuid.uuid4()}",
                    actor_id=actor_id,
                )
            row = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
        return self._profile(row)

    def adjust_credits(
        self,
        organization_id: int,
        amount: int,
        *,
        description: str,
        actor_id: int,
    ) -> dict[str, Any]:
        if not amount:
            raise SaaSError("Informe uma quantidade diferente de zero.", 422)
        now = utc_now()
        with self._transaction():
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Organização não encontrada.", 404)
            if profile["unlimited_credits"]:
                raise SaaSError("Organizações com créditos ilimitados não possuem saldo ajustável.", 409)
            balance = profile["credit_balance"] + amount
            if balance < 0:
                raise SaaSError("O ajuste deixaria o saldo de créditos negativo.", 409)
            self._connection.execute(
                "UPDATE organization_profiles SET credit_balance=?,updated_at=? WHERE organization_id=?",
                (balance, now, organization_id),
            )
            self._insert_ledger(
                organization_id,
                delta=amount,
                balance_after=balance,
                kind="manual_adjustment",
                description=description,
                idempotency_key=f"manual:{uuid.uuid4()}",
                actor_id=actor_id,
            )
            row = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
        return self._profile(row)

    @staticmethod
    def _saved_search(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["filters"] = json.loads(result.pop("filters_json"))
        return result

    def create_saved_search(
        self,
        organization_id: int,
        actor_id: int,
        *,
        name: str,
        filters: dict[str, Any],
        result_count: int | None = None,
    ) -> dict[str, Any]:
        search_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO saved_searches(
                     id,organization_id,created_by,name,filters_json,last_result_count,
                     last_run_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    search_id, organization_id, actor_id, name,
                    json.dumps(filters, ensure_ascii=False, sort_keys=True), result_count,
                    now if result_count is not None else None, now, now,
                ),
            )
            row = self._connection.execute("SELECT * FROM saved_searches WHERE id=?", (search_id,)).fetchone()
        return self._saved_search(row)

    def list_saved_searches(self, organization_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM saved_searches WHERE organization_id=? ORDER BY updated_at DESC",
                (organization_id,),
            ).fetchall()
        return [self._saved_search(row) for row in rows]

    def saved_search(self, organization_id: int, search_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM saved_searches WHERE id=? AND organization_id=?",
                (search_id, organization_id),
            ).fetchone()
        return self._saved_search(row) if row else None

    def record_saved_search_run(self, organization_id: int, search_id: str, result_count: int) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE saved_searches SET last_result_count=?,last_run_at=?,updated_at=?
                   WHERE id=? AND organization_id=?""",
                (result_count, now, now, search_id, organization_id),
            )
            if not cursor.rowcount:
                raise SaaSError("Busca salva não encontrada.", 404)
            row = self._connection.execute("SELECT * FROM saved_searches WHERE id=?", (search_id,)).fetchone()
        return self._saved_search(row)

    def delete_saved_search(self, organization_id: int, search_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM saved_searches WHERE id=? AND organization_id=?",
                (search_id, organization_id),
            )
        return bool(cursor.rowcount)

    @staticmethod
    def _company_list(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def create_company_list(
        self,
        organization_id: int,
        actor_id: int,
        *,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        list_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO company_lists(
                     id,organization_id,created_by,name,description,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (list_id, organization_id, actor_id, name, description, now, now),
            )
        return self.company_list(organization_id, list_id)

    def list_company_lists(self, organization_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT l.*,count(i.cnpj) AS company_count
                   FROM company_lists l
                   LEFT JOIN company_list_items i ON i.list_id=l.id
                   WHERE l.organization_id=?
                   GROUP BY l.id ORDER BY l.updated_at DESC""",
                (organization_id,),
            ).fetchall()
        return [self._company_list(row) for row in rows]

    def company_list(self, organization_id: int, list_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT l.*,count(i.cnpj) AS company_count
                   FROM company_lists l
                   LEFT JOIN company_list_items i ON i.list_id=l.id
                   WHERE l.id=? AND l.organization_id=? GROUP BY l.id""",
                (list_id, organization_id),
            ).fetchone()
        return self._company_list(row) if row else None

    def company_list_detail(self, organization_id: int, list_id: str) -> dict[str, Any] | None:
        company_list = self.company_list(organization_id, list_id)
        if not company_list:
            return None
        with self._lock:
            rows = self._connection.execute(
                """SELECT cnpj,company_json,added_by,added_at
                   FROM company_list_items
                   WHERE list_id=? AND organization_id=? ORDER BY added_at DESC""",
                (list_id, organization_id),
            ).fetchall()
        company_list["companies"] = [
            {**json.loads(row["company_json"]), "added_at": row["added_at"], "added_by": row["added_by"]}
            for row in rows
        ]
        return company_list

    def add_companies(
        self,
        organization_id: int,
        list_id: str,
        actor_id: int,
        companies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        unique = {company["cnpj"]: company for company in companies}
        now = utc_now()
        with self._transaction():
            company_list = self._connection.execute(
                "SELECT * FROM company_lists WHERE id=? AND organization_id=?",
                (list_id, organization_id),
            ).fetchone()
            if not company_list:
                raise SaaSError("Lista não encontrada.", 404)
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Configuração comercial da organização não encontrada.", 404)
            unlocked = self._unlocked_cnpjs(organization_id, list(unique))
            new_unlocks = [cnpj for cnpj in unique if cnpj not in unlocked]
            credits_spent = 0 if profile["unlimited_credits"] else len(new_unlocks)
            if credits_spent > profile["credit_balance"]:
                raise SaaSError(
                    f"Créditos insuficientes. Esta ação precisa de {credits_spent} e o saldo é {profile['credit_balance']}.",
                    402,
                )
            balance = profile["credit_balance"] - credits_spent
            if credits_spent:
                self._connection.execute(
                    "UPDATE organization_profiles SET credit_balance=?,updated_at=? WHERE organization_id=?",
                    (balance, now, organization_id),
                )
                self._insert_ledger(
                    organization_id,
                    delta=-credits_spent,
                    balance_after=balance,
                    kind="company_unlock",
                    description=f"{credits_spent} empresa(s) adicionada(s) a uma lista",
                    reference_id=list_id,
                    actor_id=actor_id,
                )
            self._connection.executemany(
                "INSERT INTO company_unlocks(organization_id,cnpj,unlocked_by,unlocked_at) VALUES(?,?,?,?)",
                [(organization_id, cnpj, actor_id, now) for cnpj in new_unlocks],
            )
            before = self._connection.execute(
                "SELECT count(*) FROM company_list_items WHERE list_id=?",
                (list_id,),
            ).fetchone()[0]
            self._connection.executemany(
                """INSERT INTO company_list_items(
                     list_id,organization_id,cnpj,company_json,added_by,added_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(list_id,cnpj) DO UPDATE SET company_json=excluded.company_json""",
                [
                    (
                        list_id, organization_id, cnpj,
                        json.dumps(company, ensure_ascii=False, sort_keys=True), actor_id, now,
                    )
                    for cnpj, company in unique.items()
                ],
            )
            after = self._connection.execute(
                "SELECT count(*) FROM company_list_items WHERE list_id=?",
                (list_id,),
            ).fetchone()[0]
            self._connection.execute("UPDATE company_lists SET updated_at=? WHERE id=?", (now, list_id))
        return {
            "list_id": list_id,
            "added": after - before,
            "total": after,
            "credits_spent": credits_spent,
            "credit_balance": balance,
            "unlimited_credits": bool(profile["unlimited_credits"]),
        }

    def remove_company(self, organization_id: int, list_id: str, cnpj: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM company_list_items WHERE list_id=? AND organization_id=? AND cnpj=?",
                (list_id, organization_id, cnpj),
            )
            if cursor.rowcount:
                self._connection.execute("UPDATE company_lists SET updated_at=? WHERE id=?", (utc_now(), list_id))
        return bool(cursor.rowcount)

    def delete_company_list(self, organization_id: int, list_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM company_lists WHERE id=? AND organization_id=?",
                (list_id, organization_id),
            )
        return bool(cursor.rowcount)
