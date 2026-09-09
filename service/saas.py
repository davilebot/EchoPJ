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
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                action_tab TEXT,
                deduplication_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                UNIQUE(organization_id, user_id, deduplication_key)
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user_created
                ON notifications(organization_id, user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS notification_states (
                organization_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                state_key TEXT NOT NULL,
                state_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(organization_id, user_id, state_key)
            );
            CREATE TABLE IF NOT EXISTS product_events (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                user_id INTEGER,
                event_name TEXT NOT NULL,
                subject_type TEXT,
                subject_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                deduplication_key TEXT,
                occurred_at TEXT NOT NULL,
                UNIQUE(organization_id, deduplication_key)
            );
            CREATE INDEX IF NOT EXISTS idx_product_events_org_occurred
                ON product_events(organization_id, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_product_events_name_occurred
                ON product_events(event_name, occurred_at DESC);
            CREATE TABLE IF NOT EXISTS billing_orders (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                provider TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('subscription','credit_pack')),
                plan_code TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                price_cents INTEGER NOT NULL CHECK(price_cents > 0),
                credits INTEGER NOT NULL CHECK(credits > 0),
                cycle TEXT,
                status TEXT NOT NULL,
                client_key TEXT,
                external_reference TEXT NOT NULL UNIQUE,
                provider_checkout_id TEXT,
                provider_subscription_id TEXT,
                checkout_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paid_at TEXT,
                UNIQUE(organization_id, client_key)
            );
            CREATE INDEX IF NOT EXISTS idx_billing_orders_org_created
                ON billing_orders(organization_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_billing_orders_checkout
                ON billing_orders(provider, provider_checkout_id);
            CREATE INDEX IF NOT EXISTS idx_billing_orders_subscription
                ON billing_orders(provider, provider_subscription_id);
            CREATE TABLE IF NOT EXISTS billing_webhook_events (
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                external_reference TEXT,
                object_id TEXT,
                processing_status TEXT NOT NULL,
                detail TEXT,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                PRIMARY KEY(provider, event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_billing_webhook_received
                ON billing_webhook_events(received_at DESC);
        """)
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def health_check(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1").fetchone()[0] == 1

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
                self._insert_product_event(
                    organization_id,
                    None,
                    "workspace.provisioned",
                    subject_type="organization",
                    subject_id=str(organization_id),
                    deduplication_key=f"workspace.provisioned:{organization_id}",
                    occurred_at=now,
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

    def _insert_product_event(
        self,
        organization_id: int,
        user_id: int | None,
        event_name: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        deduplication_key: str | None = None,
        occurred_at: str | None = None,
    ) -> bool:
        """Insert an append-only product event while the caller owns a transaction."""
        cursor = self._connection.execute(
            """INSERT OR IGNORE INTO product_events(
                 id,organization_id,user_id,event_name,subject_type,subject_id,
                 metadata_json,deduplication_key,occurred_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), organization_id, user_id, event_name,
                subject_type, subject_id,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                deduplication_key, occurred_at or utc_now(),
            ),
        )
        return bool(cursor.rowcount)

    def record_product_event(
        self,
        organization_id: int,
        user_id: int | None,
        event_name: str,
        **kwargs: Any,
    ) -> bool:
        """Record a meaningful customer action for activation and retention analysis."""
        with self._lock, self._connection:
            return self._insert_product_event(
                organization_id, user_id, event_name, **kwargs
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
            orders = [self._billing_order(row) for row in self._connection.execute(
                """SELECT * FROM billing_orders WHERE organization_id=?
                   ORDER BY created_at DESC LIMIT 20""",
                (organization_id,),
            ).fetchall()]
        return {
            "profile": self._profile(profile),
            "ledger": ledger,
            "unlocked_companies": unlocked,
            "orders": orders,
        }

    @staticmethod
    def _billing_order(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result.pop("client_key", None)
        result.pop("external_reference", None)
        return result

    def create_billing_order(
        self,
        organization_id: int,
        actor_id: int,
        *,
        provider: str,
        kind: str,
        plan_code: str,
        plan_name: str,
        price_cents: int,
        credits: int,
        cycle: str | None,
        client_key: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"subscription", "credit_pack"} or price_cents <= 0 or credits <= 0:
            raise SaaSError("Oferta de cobrança inválida.", 422)
        now = utc_now()
        order_id = str(uuid.uuid4())
        external_reference = f"echopjs-order:{order_id}"
        normalized_key = client_key.strip()[:120] if client_key and client_key.strip() else None
        with self._transaction():
            if normalized_key:
                previous = self._connection.execute(
                    "SELECT * FROM billing_orders WHERE organization_id=? AND client_key=?",
                    (organization_id, normalized_key),
                ).fetchone()
                if previous:
                    result = self._billing_order(previous)
                    result["external_reference"] = previous["external_reference"]
                    return result
            self._connection.execute(
                """INSERT INTO billing_orders(
                     id,organization_id,created_by,provider,kind,plan_code,plan_name,
                     price_cents,credits,cycle,status,client_key,external_reference,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_id, organization_id, actor_id, provider, kind, plan_code, plan_name,
                    price_cents, credits, cycle, "creating", normalized_key, external_reference, now, now,
                ),
            )
            row = self._connection.execute("SELECT * FROM billing_orders WHERE id=?", (order_id,)).fetchone()
        result = self._billing_order(row)
        result["external_reference"] = external_reference
        return result

    def billing_checkout_created(
        self,
        organization_id: int,
        order_id: str,
        *,
        provider_checkout_id: str,
        checkout_url: str,
    ) -> dict[str, Any]:
        with self._transaction():
            self._connection.execute(
                """UPDATE billing_orders SET provider_checkout_id=?,checkout_url=?,status='pending',updated_at=?
                   WHERE id=? AND organization_id=?""",
                (provider_checkout_id, checkout_url, utc_now(), order_id, organization_id),
            )
            row = self._connection.execute(
                "SELECT * FROM billing_orders WHERE id=? AND organization_id=?",
                (order_id, organization_id),
            ).fetchone()
            if not row:
                raise SaaSError("Pedido de cobrança não encontrado.", 404)
        return self._billing_order(row)

    def billing_checkout_failed(self, organization_id: int, order_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE billing_orders SET status='failed',updated_at=?
                   WHERE id=? AND organization_id=? AND status='creating'""",
                (utc_now(), order_id, organization_id),
            )

    def _billing_order_for_event(
        self,
        provider: str,
        external_reference: str | None,
        provider_checkout_id: str | None,
        provider_subscription_id: str | None,
    ) -> sqlite3.Row | None:
        if external_reference:
            row = self._connection.execute(
                "SELECT * FROM billing_orders WHERE provider=? AND external_reference=?",
                (provider, external_reference),
            ).fetchone()
            if row:
                return row
        if provider_checkout_id:
            row = self._connection.execute(
                "SELECT * FROM billing_orders WHERE provider=? AND provider_checkout_id=?",
                (provider, provider_checkout_id),
            ).fetchone()
            if row:
                return row
        if provider_subscription_id:
            return self._connection.execute(
                "SELECT * FROM billing_orders WHERE provider=? AND provider_subscription_id=?",
                (provider, provider_subscription_id),
            ).fetchone()
        return None

    def process_billing_event(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        payload_digest: str,
        external_reference: str | None = None,
        provider_checkout_id: str | None = None,
        provider_payment_id: str | None = None,
        provider_subscription_id: str | None = None,
        amount_cents: int | None = None,
    ) -> dict[str, Any]:
        """Persist first, then apply a small idempotent state transition."""
        now = utc_now()
        object_id = provider_payment_id or provider_subscription_id or provider_checkout_id
        with self._transaction():
            inserted = self._connection.execute(
                """INSERT OR IGNORE INTO billing_webhook_events(
                     provider,event_id,event_type,payload_digest,external_reference,object_id,
                     processing_status,received_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (provider, event_id, event_type, payload_digest, external_reference, object_id, "received", now),
            )
            duplicate = not bool(inserted.rowcount)
            if duplicate:
                existing = self._connection.execute(
                    "SELECT processing_status FROM billing_webhook_events WHERE provider=? AND event_id=?",
                    (provider, event_id),
                ).fetchone()
                if existing["processing_status"] != "received":
                    return {"accepted": True, "duplicate": True, "status": existing["processing_status"]}
            order = self._billing_order_for_event(
                provider, external_reference, provider_checkout_id, provider_subscription_id,
            )
            if not order:
                self._connection.execute(
                    """UPDATE billing_webhook_events SET processing_status='ignored',detail=?,processed_at=?
                       WHERE provider=? AND event_id=?""",
                    ("Pedido não localizado para conciliação.", now, provider, event_id),
                )
                return {"accepted": True, "duplicate": False, "matched": False}
            order_id = order["id"]
            if provider_checkout_id and not order["provider_checkout_id"]:
                self._connection.execute(
                    "UPDATE billing_orders SET provider_checkout_id=?,updated_at=? WHERE id=?",
                    (provider_checkout_id, now, order_id),
                )
            if provider_subscription_id and not order["provider_subscription_id"]:
                self._connection.execute(
                    "UPDATE billing_orders SET provider_subscription_id=?,updated_at=? WHERE id=?",
                    (provider_subscription_id, now, order_id),
                )
            status = None
            if event_type == "CHECKOUT_PAID":
                status = "checkout_paid"
            elif event_type in {"CHECKOUT_CANCELED", "SUBSCRIPTION_DELETED", "SUBSCRIPTION_INACTIVATED"}:
                status = "canceled"
            elif event_type == "CHECKOUT_EXPIRED":
                status = "expired"
            elif event_type in {"PAYMENT_OVERDUE", "PAYMENT_DUNNING_RECEIVED"}:
                status = "past_due"
            if status:
                self._connection.execute(
                    "UPDATE billing_orders SET status=?,updated_at=? WHERE id=?",
                    (status, now, order_id),
                )
        financial = event_type in {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED_IN_CASH"}
        if financial and not provider_payment_id:
            with self._transaction():
                self._connection.execute(
                    "UPDATE billing_orders SET status='needs_review',updated_at=? WHERE id=?",
                    (utc_now(), order_id),
                )
                self._connection.execute(
                    """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                       WHERE provider=? AND event_id=?""",
                    ("Confirmação financeira sem identificador de pagamento.", utc_now(), provider, event_id),
                )
            return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True, "order_id": order_id}
        if financial and amount_cents != order["price_cents"]:
            with self._transaction():
                self._connection.execute(
                    "UPDATE billing_orders SET status='needs_review',updated_at=? WHERE id=?",
                    (utc_now(), order_id),
                )
                self._connection.execute(
                    """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                       WHERE provider=? AND event_id=?""",
                    ("Valor recebido diverge do pedido.", utc_now(), provider, event_id),
                )
            return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True, "order_id": order_id}
        if financial and provider_payment_id:
            description = (
                f"Renovação do plano {order['plan_name']}"
                if order["kind"] == "subscription" else f"Compra de {order['credits']} créditos"
            )
            self.grant_credits(
                order["organization_id"],
                order["credits"],
                description=description,
                idempotency_key=f"{provider}:payment:{provider_payment_id}",
                reference_id=provider_payment_id,
            )
            if order["kind"] == "subscription":
                self.update_billing_profile(
                    order["organization_id"],
                    plan_code=order["plan_code"],
                    subscription_status="active",
                    unlimited_credits=False,
                )
            with self._transaction():
                self._connection.execute(
                    "UPDATE billing_orders SET status='paid',paid_at=coalesce(paid_at,?),updated_at=? WHERE id=?",
                    (utc_now(), utc_now(), order_id),
                )
        elif order["kind"] == "subscription" and event_type == "PAYMENT_OVERDUE":
            self.update_billing_profile(
                order["organization_id"], plan_code=order["plan_code"],
                subscription_status="past_due", unlimited_credits=False,
            )
        elif order["kind"] == "subscription" and event_type in {"SUBSCRIPTION_DELETED", "SUBSCRIPTION_INACTIVATED"}:
            self.update_billing_profile(
                order["organization_id"], plan_code=order["plan_code"],
                subscription_status="canceled", unlimited_credits=False,
            )
        with self._transaction():
            self._connection.execute(
                """UPDATE billing_webhook_events SET processing_status='processed',detail=NULL,processed_at=?
                   WHERE provider=? AND event_id=?""",
                (utc_now(), provider, event_id),
            )
        return {"accepted": True, "duplicate": duplicate, "matched": True, "order_id": order_id}

    def admin_billing_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._connection.execute(
                """SELECT provider,event_id,event_type,external_reference,object_id,
                          processing_status,detail,received_at,processed_at
                   FROM billing_webhook_events ORDER BY received_at DESC LIMIT ?""",
                (max(1, min(500, limit)),),
            ).fetchall()]

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
            self._insert_product_event(
                organization_id,
                actor_id,
                "companies.exported",
                subject_type=kind,
                subject_id=reference_id,
                metadata={
                    "requested": len(unique),
                    "newly_unlocked": len(new_unlocks),
                    "credits_spent": credits_spent,
                },
                occurred_at=now,
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

    def admin_product_activity(self) -> dict[int, dict[str, Any]]:
        """Return cross-tenant product signals for authorized internal reporting."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT p.*,
                   (SELECT count(*) FROM saved_searches s
                    WHERE s.organization_id=p.organization_id) AS saved_search_count,
                   (SELECT count(*) FROM company_lists l
                    WHERE l.organization_id=p.organization_id) AS list_count,
                   (SELECT count(*) FROM company_unlocks u
                    WHERE u.organization_id=p.organization_id) AS unlocked_companies,
                   (SELECT count(*) FROM product_events e
                    WHERE e.organization_id=p.organization_id
                      AND e.event_name='search.executed') AS search_count,
                   (SELECT count(*) FROM product_events e
                    WHERE e.organization_id=p.organization_id
                      AND e.event_name='job.created') AS job_count,
                   (SELECT count(*) FROM product_events e
                    WHERE e.organization_id=p.organization_id
                      AND e.event_name LIKE 'team.invitation%') AS invitation_actions
                   FROM organization_profiles p"""
            ).fetchall()
            activity_rows = self._connection.execute(
                """WITH activity(organization_id,occurred_at) AS (
                       SELECT organization_id,occurred_at FROM product_events
                        WHERE event_name NOT LIKE 'workspace.%'
                       UNION ALL SELECT organization_id,created_at FROM saved_searches
                       UNION ALL SELECT organization_id,last_run_at FROM saved_searches
                        WHERE last_run_at IS NOT NULL
                       UNION ALL SELECT organization_id,created_at FROM company_lists
                       UNION ALL SELECT organization_id,added_at FROM company_list_items
                       UNION ALL SELECT organization_id,unlocked_at FROM company_unlocks
                   )
                   SELECT organization_id,count(*) AS action_count,
                          count(DISTINCT substr(occurred_at,1,10)) AS activity_days,
                          max(occurred_at) AS last_activity_at
                   FROM activity GROUP BY organization_id"""
            ).fetchall()
        activity = {row["organization_id"]: dict(row) for row in activity_rows}
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            current = dict(row)
            organization_id = current.pop("organization_id")
            current["unlimited_credits"] = bool(current["unlimited_credits"])
            current.update(activity.get(organization_id, {
                "action_count": 0,
                "activity_days": 0,
                "last_activity_at": None,
            }))
            result[organization_id] = current
        return result

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

    def _insert_notification(
        self,
        organization_id: int,
        user_id: int,
        *,
        kind: str,
        title: str,
        message: str,
        action_tab: str | None,
        deduplication_key: str,
    ) -> None:
        self._connection.execute(
            """INSERT OR IGNORE INTO notifications(
                 id,organization_id,user_id,kind,title,message,action_tab,
                 deduplication_key,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), organization_id, user_id, kind,
                title[:120], message[:500], action_tab, deduplication_key, utc_now(),
            ),
        )

    def sync_notifications(
        self,
        organization_id: int,
        user_id: int,
        *,
        jobs: list[dict[str, Any]],
        low_credit_threshold: int = 20,
    ) -> None:
        """Materialize deterministic alerts from current operational state."""
        threshold = max(0, int(low_credit_threshold))
        with self._transaction():
            for job in jobs:
                if job.get("status") not in {"completed", "completed_with_errors"}:
                    continue
                warning = job["status"] == "completed_with_errors"
                self._insert_notification(
                    organization_id,
                    user_id,
                    kind="job_warning" if warning else "job_completed",
                    title="Processamento concluído com avisos" if warning else "Processamento concluído",
                    message=f"{str(job.get('filename') or 'Arquivo')[:180]} · {int(job.get('processed') or 0)} linhas processadas.",
                    action_tab="history",
                    deduplication_key=f"job-finished:{job['id']}",
                )

            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
            if not profile:
                raise SaaSError("Configuração comercial da organização não encontrada.", 404)
            state_key = "low-credit-episode"
            state = self._connection.execute(
                """SELECT state_value FROM notification_states
                   WHERE organization_id=? AND user_id=? AND state_key=?""",
                (organization_id, user_id, state_key),
            ).fetchone()
            low_balance = not profile["unlimited_credits"] and profile["credit_balance"] <= threshold
            if low_balance and not state:
                episode = str(uuid.uuid4())
                now = utc_now()
                self._connection.execute(
                    "INSERT INTO notification_states VALUES(?,?,?,?,?)",
                    (organization_id, user_id, state_key, episode, now),
                )
                self._insert_notification(
                    organization_id,
                    user_id,
                    kind="low_credit",
                    title="Créditos perto do fim",
                    message=f"A organização tem {profile['credit_balance']} créditos disponíveis.",
                    action_tab="billing",
                    deduplication_key=f"low-credit:{episode}",
                )
            elif not low_balance and state:
                self._connection.execute(
                    "DELETE FROM notification_states WHERE organization_id=? AND user_id=? AND state_key=?",
                    (organization_id, user_id, state_key),
                )

    def list_notifications(
        self,
        organization_id: int,
        user_id: int,
        *,
        limit: int = 30,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = [dict(row) for row in self._connection.execute(
                """SELECT id,kind,title,message,action_tab,created_at,read_at
                   FROM notifications WHERE organization_id=? AND user_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (organization_id, user_id, limit),
            ).fetchall()]
            unread = self._connection.execute(
                """SELECT count(*) FROM notifications
                   WHERE organization_id=? AND user_id=? AND read_at IS NULL""",
                (organization_id, user_id),
            ).fetchone()[0]
        return {"notifications": rows, "unread_count": unread}

    def mark_notification_read(self, organization_id: int, user_id: int, notification_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE notifications SET read_at=COALESCE(read_at,?)
                   WHERE id=? AND organization_id=? AND user_id=?""",
                (utc_now(), notification_id, organization_id, user_id),
            )
        return bool(cursor.rowcount)

    def mark_all_notifications_read(self, organization_id: int, user_id: int) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE notifications SET read_at=?
                   WHERE organization_id=? AND user_id=? AND read_at IS NULL""",
                (utc_now(), organization_id, user_id),
            )
        return cursor.rowcount

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
            self._insert_product_event(
                organization_id,
                actor_id,
                "saved_search.created",
                subject_type="saved_search",
                subject_id=search_id,
                metadata={"has_result_count": result_count is not None},
                deduplication_key=f"saved_search.created:{search_id}",
                occurred_at=now,
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

    def record_saved_search_run(
        self,
        organization_id: int,
        search_id: str,
        result_count: int,
        *,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE saved_searches SET last_result_count=?,last_run_at=?,updated_at=?
                   WHERE id=? AND organization_id=?""",
                (result_count, now, now, search_id, organization_id),
            )
            if not cursor.rowcount:
                raise SaaSError("Busca salva não encontrada.", 404)
            self._insert_product_event(
                organization_id,
                actor_id,
                "saved_search.executed",
                subject_type="saved_search",
                subject_id=search_id,
                metadata={"result_count": result_count},
                occurred_at=now,
            )
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
            self._insert_product_event(
                organization_id,
                actor_id,
                "company_list.created",
                subject_type="company_list",
                subject_id=list_id,
                deduplication_key=f"company_list.created:{list_id}",
                occurred_at=now,
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
            self._insert_product_event(
                organization_id,
                actor_id,
                "company_list.companies_added",
                subject_type="company_list",
                subject_id=list_id,
                metadata={
                    "submitted": len(unique),
                    "added": after - before,
                    "newly_unlocked": len(new_unlocks),
                    "credits_spent": credits_spent,
                },
                occurred_at=now,
            )
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
