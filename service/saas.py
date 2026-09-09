"""Operational SaaS data isolated from the shared Receita database."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
            CREATE TABLE IF NOT EXISTS billing_subscriptions (
                provider TEXT NOT NULL,
                provider_subscription_id TEXT NOT NULL,
                organization_id INTEGER NOT NULL,
                order_id TEXT NOT NULL REFERENCES billing_orders(id),
                plan_code TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                credits_per_cycle INTEGER NOT NULL CHECK(credits_per_cycle > 0),
                cycle TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_status TEXT,
                next_due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                canceled_at TEXT,
                PRIMARY KEY(provider, provider_subscription_id)
            );
            CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_org_updated
                ON billing_subscriptions(organization_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS billing_payments (
                provider TEXT NOT NULL,
                provider_payment_id TEXT NOT NULL,
                organization_id INTEGER NOT NULL,
                order_id TEXT NOT NULL REFERENCES billing_orders(id),
                provider_subscription_id TEXT,
                amount_cents INTEGER,
                status TEXT NOT NULL,
                provider_status TEXT,
                billing_type TEXT,
                due_date TEXT,
                credits_granted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paid_at TEXT,
                PRIMARY KEY(provider, provider_payment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_billing_payments_org_updated
                ON billing_payments(organization_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS billing_subscription_actions (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                requested_by INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_subscription_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('cancel')),
                status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
                reason TEXT NOT NULL DEFAULT '',
                detail TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_billing_subscription_actions_org_created
                ON billing_subscription_actions(organization_id, created_at DESC);
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
            CREATE TABLE IF NOT EXISTS support_tickets (
                id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                opened_by INTEGER NOT NULL,
                requester_identifier TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('question','technical','billing','suggestion')),
                priority TEXT NOT NULL CHECK(priority IN ('low','normal','high','urgent')),
                status TEXT NOT NULL CHECK(status IN ('open','in_progress','waiting_customer','resolved','closed')),
                subject TEXT NOT NULL,
                diagnostic_json TEXT NOT NULL DEFAULT '{}',
                assigned_to INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_message_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_support_tickets_org_updated
                ON support_tickets(organization_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_support_tickets_status_updated
                ON support_tickets(status, priority, updated_at DESC);
            CREATE TABLE IF NOT EXISTS support_messages (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
                organization_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                author_kind TEXT NOT NULL CHECK(author_kind IN ('customer','support')),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_support_messages_ticket_created
                ON support_messages(ticket_id, created_at);
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
            subscription = self._connection.execute(
                """SELECT * FROM billing_subscriptions WHERE organization_id=?
                   ORDER BY CASE status
                     WHEN 'active' THEN 0 WHEN 'past_due' THEN 1 WHEN 'pending' THEN 2
                     WHEN 'inactive' THEN 3 ELSE 4 END, updated_at DESC LIMIT 1""",
                (organization_id,),
            ).fetchone()
            payments = [self._billing_payment(row) for row in self._connection.execute(
                """SELECT p.*,o.plan_name FROM billing_payments p
                   JOIN billing_orders o ON o.id=p.order_id
                   WHERE p.organization_id=? ORDER BY p.updated_at DESC LIMIT 20""",
                (organization_id,),
            ).fetchall()]
            cancellation = self._connection.execute(
                """SELECT id,status,reason,detail,created_at,completed_at
                   FROM billing_subscription_actions
                   WHERE organization_id=? AND action='cancel'
                   ORDER BY created_at DESC LIMIT 1""",
                (organization_id,),
            ).fetchone()
        return {
            "profile": self._profile(profile),
            "ledger": ledger,
            "unlocked_companies": unlocked,
            "orders": orders,
            "subscription": self._billing_subscription(subscription),
            "payments": payments,
            "cancellation": dict(cancellation) if cancellation else None,
        }

    @staticmethod
    def _billing_order(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result.pop("client_key", None)
        result.pop("external_reference", None)
        return result

    @staticmethod
    def _billing_subscription(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "provider": row["provider"],
            "provider_subscription_id": row["provider_subscription_id"],
            "plan_code": row["plan_code"],
            "plan_name": row["plan_name"],
            "credits_per_cycle": row["credits_per_cycle"],
            "cycle": row["cycle"],
            "status": row["status"],
            "next_due_date": row["next_due_date"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "canceled_at": row["canceled_at"],
        }

    @staticmethod
    def _billing_payment(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "provider_payment_id": row["provider_payment_id"],
            "plan_name": row["plan_name"],
            "amount_cents": row["amount_cents"],
            "status": row["status"],
            "billing_type": row["billing_type"],
            "due_date": row["due_date"],
            "credits_granted": row["credits_granted"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "paid_at": row["paid_at"],
        }

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
                    if (
                        previous["provider"] != provider
                        or previous["kind"] != kind
                        or previous["plan_code"] != plan_code
                        or previous["price_cents"] != price_cents
                        or previous["credits"] != credits
                        or previous["cycle"] != cycle
                    ):
                        raise SaaSError("Esta chave de repetição já foi usada para outra oferta.", 409)
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

    def current_billing_subscription(self, organization_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM billing_subscriptions WHERE organization_id=?
                   ORDER BY CASE status
                     WHEN 'active' THEN 0 WHEN 'past_due' THEN 1 WHEN 'pending' THEN 2
                     WHEN 'inactive' THEN 3 ELSE 4 END, updated_at DESC LIMIT 1""",
                (organization_id,),
            ).fetchone()
        return self._billing_subscription(row)

    def subscription_purchase_blocker(
        self, organization_id: int, *, client_key: str | None = None
    ) -> dict[str, Any] | None:
        """Prevent parallel recurring contracts and accidental repeated checkouts."""
        with self._lock:
            subscription = self._connection.execute(
                """SELECT plan_name,status FROM billing_subscriptions WHERE organization_id=?
                   AND status IN ('active','past_due','pending','inactive')
                   ORDER BY updated_at DESC LIMIT 1""",
                (organization_id,),
            ).fetchone()
            if subscription:
                return {"kind": "subscription", **dict(subscription)}
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            order = self._connection.execute(
                """SELECT plan_name,status FROM billing_orders WHERE organization_id=?
                   AND kind='subscription' AND status IN ('creating','pending','checkout_paid')
                   AND created_at>=? AND (? IS NULL OR client_key IS NULL OR client_key<>?)
                   ORDER BY created_at DESC LIMIT 1""",
                (organization_id, cutoff, client_key, client_key),
            ).fetchone()
        return {"kind": "checkout", **dict(order)} if order else None

    def begin_subscription_cancellation(
        self, organization_id: int, actor_id: int, *, reason: str = ""
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            subscription = self._connection.execute(
                """SELECT * FROM billing_subscriptions WHERE organization_id=?
                   AND status IN ('active','past_due','inactive')
                   ORDER BY updated_at DESC LIMIT 1""",
                (organization_id,),
            ).fetchone()
            if not subscription:
                raise SaaSError("Não há uma assinatura recorrente ativa para cancelar.", 409)
            pending = self._connection.execute(
                """SELECT id FROM billing_subscription_actions
                   WHERE organization_id=? AND provider=? AND provider_subscription_id=?
                   AND action='cancel' AND status='pending' LIMIT 1""",
                (organization_id, subscription["provider"], subscription["provider_subscription_id"]),
            ).fetchone()
            if pending:
                raise SaaSError("O cancelamento desta assinatura já está em andamento.", 409)
            action_id = str(uuid.uuid4())
            self._connection.execute(
                """INSERT INTO billing_subscription_actions(
                     id,organization_id,requested_by,provider,provider_subscription_id,
                     action,status,reason,created_at
                   ) VALUES(?,?,?,?,?,'cancel','pending',?,?)""",
                (
                    action_id, organization_id, actor_id, subscription["provider"],
                    subscription["provider_subscription_id"], reason.strip()[:500], now,
                ),
            )
        return {
            "id": action_id,
            "provider": subscription["provider"],
            "provider_subscription_id": subscription["provider_subscription_id"],
            "status": "pending",
        }

    def fail_subscription_cancellation(self, action_id: str, detail: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE billing_subscription_actions SET status='failed',detail=?,completed_at=?
                   WHERE id=? AND status='pending'""",
                (detail.strip()[:500], utc_now(), action_id),
            )

    def complete_subscription_cancellation(self, action_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            action = self._connection.execute(
                "SELECT * FROM billing_subscription_actions WHERE id=?",
                (action_id,),
            ).fetchone()
            if not action:
                raise SaaSError("Solicitação de cancelamento não encontrada.", 404)
            subscription = self._connection.execute(
                """SELECT * FROM billing_subscriptions
                   WHERE provider=? AND provider_subscription_id=?""",
                (action["provider"], action["provider_subscription_id"]),
            ).fetchone()
            if not subscription or subscription["organization_id"] != action["organization_id"]:
                raise SaaSError("Assinatura não encontrada para concluir o cancelamento.", 404)
            self._connection.execute(
                """UPDATE billing_subscription_actions
                   SET status='completed',detail=NULL,completed_at=? WHERE id=?""",
                (now, action_id),
            )
            self._connection.execute(
                """UPDATE billing_subscriptions
                   SET status='canceled',provider_status='DELETED',canceled_at=?,updated_at=?
                   WHERE provider=? AND provider_subscription_id=?""",
                (now, now, action["provider"], action["provider_subscription_id"]),
            )
            profile = self._connection.execute(
                "SELECT * FROM organization_profiles WHERE organization_id=?",
                (action["organization_id"],),
            ).fetchone()
            if profile and profile["subscription_status"] != "canceled":
                self._connection.execute(
                    """UPDATE organization_profiles SET subscription_status='canceled',updated_at=?
                       WHERE organization_id=?""",
                    (now, action["organization_id"]),
                )
                self._insert_ledger(
                    action["organization_id"], delta=0,
                    balance_after=profile["credit_balance"], kind="billing_profile_update",
                    description=f"Plano {profile['plan_code']} · status canceled",
                    idempotency_key=f"billing-cancel:{action_id}", actor_id=action["requested_by"],
                )
            self._insert_product_event(
                action["organization_id"], action["requested_by"],
                "billing.subscription_canceled", subject_type="subscription",
                subject_id=action["provider_subscription_id"],
                deduplication_key=f"billing.subscription_canceled:{action_id}", occurred_at=now,
            )
            row = self._connection.execute(
                """SELECT * FROM billing_subscriptions
                   WHERE provider=? AND provider_subscription_id=?""",
                (action["provider"], action["provider_subscription_id"]),
            ).fetchone()
        return self._billing_subscription(row)

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
        payment_status: str | None = None,
        payment_due_date: str | None = None,
        payment_billing_type: str | None = None,
        subscription_status: str | None = None,
        subscription_next_due_date: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile one provider event atomically across orders, cycles and credits."""
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
            effective_subscription_id = provider_subscription_id or order["provider_subscription_id"]
            existing_subscription = None
            if effective_subscription_id and order["kind"] == "subscription":
                existing_subscription = self._connection.execute(
                    """SELECT * FROM billing_subscriptions
                       WHERE provider=? AND provider_subscription_id=?""",
                    (provider, effective_subscription_id),
                ).fetchone()
                if existing_subscription and (
                    existing_subscription["organization_id"] != order["organization_id"]
                    or existing_subscription["order_id"] != order_id
                ):
                    self._connection.execute(
                        """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                           WHERE provider=? AND event_id=?""",
                        ("Assinatura vinculada a outro pedido.", now, provider, event_id),
                    )
                    return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True}
                initial_subscription_state = existing_subscription["status"] if existing_subscription else "pending"
                self._connection.execute(
                    """INSERT INTO billing_subscriptions(
                         provider,provider_subscription_id,organization_id,order_id,plan_code,
                         plan_name,credits_per_cycle,cycle,status,provider_status,next_due_date,
                         created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(provider,provider_subscription_id) DO UPDATE SET
                         provider_status=coalesce(excluded.provider_status,billing_subscriptions.provider_status),
                         next_due_date=coalesce(excluded.next_due_date,billing_subscriptions.next_due_date),
                         updated_at=excluded.updated_at""",
                    (
                        provider, effective_subscription_id, order["organization_id"], order_id,
                        order["plan_code"], order["plan_name"], order["credits"], order["cycle"],
                        initial_subscription_state, subscription_status, subscription_next_due_date,
                        now, now,
                    ),
                )

            order_status = None
            if event_type == "CHECKOUT_PAID":
                order_status = "checkout_paid"
            elif event_type == "CHECKOUT_CANCELED":
                order_status = "canceled"
            elif event_type == "CHECKOUT_EXPIRED":
                order_status = "expired"
            if order_status and order["status"] not in {"paid", "needs_review"}:
                self._connection.execute(
                    "UPDATE billing_orders SET status=?,updated_at=? WHERE id=?",
                    (order_status, now, order_id),
                )

            financial = event_type in {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED_IN_CASH"}
            payment_states = {
                "PAYMENT_CREATED": "pending",
                "PAYMENT_AWAITING_RISK_ANALYSIS": "pending",
                "PAYMENT_APPROVED_BY_RISK_ANALYSIS": "pending",
                "PAYMENT_AUTHORIZED": "pending",
                "PAYMENT_UPDATED": "pending",
                "PAYMENT_CONFIRMED": "confirmed",
                "PAYMENT_RECEIVED": "received",
                "PAYMENT_RECEIVED_IN_CASH": "received",
                "PAYMENT_OVERDUE": "overdue",
                "PAYMENT_DUNNING_REQUESTED": "overdue",
                "PAYMENT_DUNNING_RECEIVED": "overdue",
                "PAYMENT_CREDIT_CARD_CAPTURE_REFUSED": "failed",
                "PAYMENT_REPROVED_BY_RISK_ANALYSIS": "failed",
                "PAYMENT_DELETED": "canceled",
                "PAYMENT_RESTORED": "pending",
                "PAYMENT_REFUND_IN_PROGRESS": "refund_pending",
                "PAYMENT_REFUNDED": "refunded",
                "PAYMENT_PARTIALLY_REFUNDED": "refunded",
                "PAYMENT_RECEIVED_IN_CASH_UNDONE": "refunded",
                "PAYMENT_CHARGEBACK_REQUESTED": "chargeback",
                "PAYMENT_CHARGEBACK_DISPUTE": "chargeback",
            }
            payment_state = payment_states.get(event_type)
            if provider_payment_id and payment_state:
                previous_payment = self._connection.execute(
                    """SELECT * FROM billing_payments
                       WHERE provider=? AND provider_payment_id=?""",
                    (provider, provider_payment_id),
                ).fetchone()
                if previous_payment and (
                    previous_payment["organization_id"] != order["organization_id"]
                    or previous_payment["order_id"] != order_id
                ):
                    self._connection.execute(
                        """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                           WHERE provider=? AND event_id=?""",
                        ("Pagamento vinculado a outro pedido.", now, provider, event_id),
                    )
                    return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True}
                if previous_payment and event_type != "PAYMENT_RESTORED":
                    state_rank = {
                        "pending": 0, "overdue": 1, "failed": 1, "confirmed": 2,
                        "received": 3, "canceled": 3, "refund_pending": 4,
                        "refunded": 5, "chargeback": 5, "review": 5,
                    }
                    if state_rank.get(previous_payment["status"], 0) > state_rank.get(payment_state, 0):
                        payment_state = previous_payment["status"]
                self._connection.execute(
                    """INSERT INTO billing_payments(
                         provider,provider_payment_id,organization_id,order_id,provider_subscription_id,
                         amount_cents,status,provider_status,billing_type,due_date,created_at,updated_at,paid_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(provider,provider_payment_id) DO UPDATE SET
                         provider_subscription_id=coalesce(excluded.provider_subscription_id,billing_payments.provider_subscription_id),
                         amount_cents=coalesce(excluded.amount_cents,billing_payments.amount_cents),
                         status=excluded.status,
                         provider_status=coalesce(excluded.provider_status,billing_payments.provider_status),
                         billing_type=coalesce(excluded.billing_type,billing_payments.billing_type),
                         due_date=coalesce(excluded.due_date,billing_payments.due_date),
                         updated_at=excluded.updated_at,
                         paid_at=coalesce(billing_payments.paid_at,excluded.paid_at)""",
                    (
                        provider, provider_payment_id, order["organization_id"], order_id,
                        effective_subscription_id, amount_cents, payment_state, payment_status,
                        payment_billing_type, payment_due_date, now, now, now if financial else None,
                    ),
                )

            creditable = financial and payment_state in {"confirmed", "received"}
            if financial and not provider_payment_id:
                self._connection.execute(
                    "UPDATE billing_orders SET status='needs_review',updated_at=? WHERE id=?",
                    (now, order_id),
                )
                self._connection.execute(
                    """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                       WHERE provider=? AND event_id=?""",
                    ("Confirmação financeira sem identificador de pagamento.", now, provider, event_id),
                )
                return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True, "order_id": order_id}
            if creditable and amount_cents != order["price_cents"]:
                self._connection.execute(
                    "UPDATE billing_orders SET status='needs_review',updated_at=? WHERE id=?",
                    (now, order_id),
                )
                if provider_payment_id:
                    self._connection.execute(
                        """UPDATE billing_payments SET status='review',updated_at=?
                           WHERE provider=? AND provider_payment_id=?""",
                        (now, provider, provider_payment_id),
                    )
                if effective_subscription_id:
                    self._connection.execute(
                        """UPDATE billing_subscriptions SET status='past_due',updated_at=?
                           WHERE provider=? AND provider_subscription_id=? AND status NOT IN ('inactive','canceled')""",
                        (now, provider, effective_subscription_id),
                    )
                self._connection.execute(
                    """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                       WHERE provider=? AND event_id=?""",
                    ("Valor recebido diverge do pedido.", now, provider, event_id),
                )
                profile = self._connection.execute(
                    "SELECT * FROM organization_profiles WHERE organization_id=?",
                    (order["organization_id"],),
                ).fetchone()
                if profile and order["kind"] == "subscription" and profile["subscription_status"] != "canceled":
                    self._connection.execute(
                        """UPDATE organization_profiles SET plan_code=?,subscription_status='past_due',
                           unlimited_credits=0,updated_at=? WHERE organization_id=?""",
                        (order["plan_code"], now, order["organization_id"]),
                    )
                return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True, "order_id": order_id}

            if creditable and provider_payment_id:
                credit_key = f"{provider}:payment:{provider_payment_id}"
                credit = self._connection.execute(
                    "SELECT organization_id FROM credit_ledger WHERE idempotency_key=?",
                    (credit_key,),
                ).fetchone()
                if credit and credit["organization_id"] != order["organization_id"]:
                    self._connection.execute(
                        """UPDATE billing_webhook_events SET processing_status='review',detail=?,processed_at=?
                           WHERE provider=? AND event_id=?""",
                        ("Identificador financeiro já utilizado por outra organização.", now, provider, event_id),
                    )
                    return {"accepted": True, "duplicate": duplicate, "matched": True, "review": True, "order_id": order_id}
                if not credit:
                    profile = self._connection.execute(
                        "SELECT * FROM organization_profiles WHERE organization_id=?",
                        (order["organization_id"],),
                    ).fetchone()
                    if not profile:
                        raise SaaSError("Organização não encontrada.", 404)
                    balance = profile["credit_balance"] + order["credits"]
                    self._connection.execute(
                        "UPDATE organization_profiles SET credit_balance=?,updated_at=? WHERE organization_id=?",
                        (balance, now, order["organization_id"]),
                    )
                    first_cycle = not bool(order["paid_at"])
                    description = (
                        f"Ativação do plano {order['plan_name']}" if order["kind"] == "subscription" and first_cycle
                        else f"Renovação do plano {order['plan_name']}" if order["kind"] == "subscription"
                        else f"Compra de {order['credits']} créditos"
                    )
                    self._insert_ledger(
                        order["organization_id"], delta=order["credits"], balance_after=balance,
                        kind="credit_grant", description=description,
                        reference_id=provider_payment_id, idempotency_key=credit_key,
                    )
                    self._insert_product_event(
                        order["organization_id"], None,
                        "billing.subscription_paid" if order["kind"] == "subscription" else "billing.credit_pack_paid",
                        subject_type="payment", subject_id=provider_payment_id,
                        metadata={"credits": order["credits"], "price_cents": order["price_cents"]},
                        deduplication_key=f"billing.payment:{provider}:{provider_payment_id}", occurred_at=now,
                    )
                self._connection.execute(
                    """UPDATE billing_payments SET credits_granted=?,updated_at=?
                       WHERE provider=? AND provider_payment_id=?""",
                    (order["credits"], now, provider, provider_payment_id),
                )
                self._connection.execute(
                    "UPDATE billing_orders SET status='paid',paid_at=coalesce(paid_at,?),updated_at=? WHERE id=?",
                    (now, now, order_id),
                )

            subscription_state = existing_subscription["status"] if existing_subscription else "pending"
            if effective_subscription_id and order["kind"] == "subscription":
                if event_type == "SUBSCRIPTION_DELETED" or subscription_status == "EXPIRED":
                    subscription_state = "canceled"
                elif event_type == "SUBSCRIPTION_INACTIVATED" or subscription_status == "INACTIVE":
                    subscription_state = "inactive"
                elif event_type == "SUBSCRIPTION_UPDATED" and subscription_status == "ACTIVE":
                    if subscription_state in {"active", "past_due", "inactive"}:
                        issue = self._connection.execute(
                            """SELECT 1 FROM billing_payments
                               WHERE provider=? AND provider_subscription_id=?
                               AND status IN ('overdue','failed','refund_pending','refunded','chargeback','review') LIMIT 1""",
                            (provider, effective_subscription_id),
                        ).fetchone()
                        subscription_state = "past_due" if issue else "active"
                elif payment_state in {"overdue", "failed", "refund_pending", "refunded", "chargeback", "review"}:
                    if subscription_state not in {"inactive", "canceled"}:
                        subscription_state = "past_due"
                elif creditable and subscription_state not in {"inactive", "canceled"}:
                    issue = self._connection.execute(
                        """SELECT 1 FROM billing_payments
                           WHERE provider=? AND provider_subscription_id=?
                           AND status IN ('overdue','failed','refund_pending','refunded','chargeback','review') LIMIT 1""",
                        (provider, effective_subscription_id),
                    ).fetchone()
                    subscription_state = "past_due" if issue else "active"
                canceled_at = now if subscription_state == "canceled" else None
                self._connection.execute(
                    """UPDATE billing_subscriptions SET status=?,provider_status=coalesce(?,provider_status),
                       next_due_date=coalesce(?,next_due_date),canceled_at=coalesce(canceled_at,?),updated_at=?
                       WHERE provider=? AND provider_subscription_id=?""",
                    (
                        subscription_state, subscription_status, subscription_next_due_date,
                        canceled_at, now, provider, effective_subscription_id,
                    ),
                )

                desired_profile_status = None
                if subscription_state in {"inactive", "canceled"}:
                    desired_profile_status = "canceled"
                elif subscription_state == "past_due":
                    desired_profile_status = "past_due"
                elif subscription_state == "active" and (
                    creditable or event_type == "SUBSCRIPTION_UPDATED"
                ):
                    desired_profile_status = "active"
                if desired_profile_status:
                    profile = self._connection.execute(
                        "SELECT * FROM organization_profiles WHERE organization_id=?",
                        (order["organization_id"],),
                    ).fetchone()
                    late_payment_after_cancel = creditable and profile and profile["subscription_status"] == "canceled" and subscription_state == "canceled"
                    if profile and not late_payment_after_cancel and (
                        profile["plan_code"] != order["plan_code"]
                        or profile["subscription_status"] != desired_profile_status
                        or profile["unlimited_credits"]
                    ):
                        self._connection.execute(
                            """UPDATE organization_profiles SET plan_code=?,subscription_status=?,
                               unlimited_credits=0,updated_at=? WHERE organization_id=?""",
                            (order["plan_code"], desired_profile_status, now, order["organization_id"]),
                        )
                        self._insert_ledger(
                            order["organization_id"], delta=0,
                            balance_after=profile["credit_balance"], kind="billing_profile_update",
                            description=f"Plano {order['plan_code']} · status {desired_profile_status}",
                            idempotency_key=f"{provider}:event:{event_id}:profile",
                        )

            review_events = {
                "PAYMENT_REFUND_IN_PROGRESS", "PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED",
                "PAYMENT_RECEIVED_IN_CASH_UNDONE", "PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_CHARGEBACK_DISPUTE",
            }
            processing_status = "review" if event_type in review_events else "processed"
            detail = "Estorno ou contestação exige conferência financeira." if processing_status == "review" else None
            if processing_status == "review":
                self._connection.execute(
                    "UPDATE billing_orders SET status='needs_review',updated_at=? WHERE id=?",
                    (now, order_id),
                )
            self._connection.execute(
                """UPDATE billing_webhook_events SET processing_status=?,detail=?,processed_at=?
                   WHERE provider=? AND event_id=?""",
                (processing_status, detail, now, provider, event_id),
            )
            return {
                "accepted": True,
                "duplicate": duplicate,
                "matched": True,
                "review": processing_status == "review",
                "order_id": order_id,
            }

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
            billing_state_key = "billing-status"
            billing_state = self._connection.execute(
                """SELECT state_value FROM notification_states
                   WHERE organization_id=? AND user_id=? AND state_key=?""",
                (organization_id, user_id, billing_state_key),
            ).fetchone()
            billing_status = profile["subscription_status"]
            billing_alerts = {
                "past_due": (
                    "Pagamento pendente",
                    "A renovação não foi confirmada. Consulte o histórico financeiro para regularizar.",
                ),
                "canceled": (
                    "Renovação cancelada",
                    "A assinatura não gerará novas cobranças. Os créditos restantes continuam disponíveis.",
                ),
                "suspended": (
                    "Acesso da organização suspenso",
                    "Abra Plano e créditos ou fale com o suporte para regularizar o workspace.",
                ),
            }
            if billing_status in billing_alerts and (
                not billing_state or billing_state["state_value"] != billing_status
            ):
                self._connection.execute(
                    """INSERT INTO notification_states VALUES(?,?,?,?,?)
                       ON CONFLICT(organization_id,user_id,state_key) DO UPDATE SET
                         state_value=excluded.state_value,updated_at=excluded.updated_at""",
                    (organization_id, user_id, billing_state_key, billing_status, utc_now()),
                )
                title, message = billing_alerts[billing_status]
                self._insert_notification(
                    organization_id, user_id, kind="billing_status",
                    title=title, message=message, action_tab="billing",
                    deduplication_key=f"billing-status:{billing_status}:{uuid.uuid4()}",
                )
            elif billing_status not in billing_alerts and billing_state:
                self._connection.execute(
                    """DELETE FROM notification_states
                       WHERE organization_id=? AND user_id=? AND state_key=?""",
                    (organization_id, user_id, billing_state_key),
                )
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
    def _support_ticket(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["diagnostic"] = json.loads(result.pop("diagnostic_json") or "{}")
        return result

    def create_support_ticket(
        self,
        organization_id: int,
        actor_id: int,
        *,
        requester_identifier: str,
        category: str,
        priority: str,
        subject: str,
        message: str,
        diagnostic: dict[str, Any],
    ) -> dict[str, Any]:
        if category not in {"question", "technical", "billing", "suggestion"}:
            raise SaaSError("Categoria de suporte inválida.", 422)
        if priority not in {"normal", "high"}:
            raise SaaSError("Prioridade de suporte inválida.", 422)
        ticket_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        now = utc_now()
        clean_subject = subject.strip()
        clean_message = message.strip()
        if len(clean_subject) < 5 or len(clean_subject) > 120 or len(clean_message) < 10 or len(clean_message) > 4000:
            raise SaaSError("Revise o assunto e a descrição do chamado.", 422)
        with self._transaction():
            self._connection.execute(
                """INSERT INTO support_tickets(
                     id,organization_id,opened_by,requester_identifier,category,priority,
                     status,subject,diagnostic_json,created_at,updated_at,last_message_at
                   ) VALUES(?,?,?,?,?,?,'open',?,?,?,?,?)""",
                (
                    ticket_id, organization_id, actor_id, requester_identifier[:254],
                    category, priority, clean_subject,
                    json.dumps(diagnostic, ensure_ascii=False, sort_keys=True),
                    now, now, now,
                ),
            )
            self._connection.execute(
                """INSERT INTO support_messages(
                     id,ticket_id,organization_id,author_id,author_kind,body,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (message_id, ticket_id, organization_id, actor_id, "customer", clean_message, now),
            )
            self._insert_product_event(
                organization_id,
                actor_id,
                "support.ticket_opened",
                subject_type="support_ticket",
                subject_id=ticket_id,
                metadata={"category": category, "priority": priority},
                occurred_at=now,
            )
            row = self._connection.execute(
                "SELECT * FROM support_tickets WHERE id=?", (ticket_id,)
            ).fetchone()
        return self._support_ticket(row)

    def list_support_tickets(self, organization_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._connection.execute(
                """SELECT t.*,
                   (SELECT count(*) FROM support_messages m WHERE m.ticket_id=t.id) AS message_count,
                   (SELECT substr(body,1,180) FROM support_messages m WHERE m.ticket_id=t.id
                    ORDER BY m.created_at DESC LIMIT 1) AS last_message_preview
                   FROM support_tickets t WHERE t.organization_id=?
                   ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1
                     WHEN 'waiting_customer' THEN 2 WHEN 'resolved' THEN 3 ELSE 4 END,
                     t.updated_at DESC LIMIT ?""",
                (organization_id, limit),
            ).fetchall()
        return [self._support_ticket(row) for row in rows]

    def support_ticket_detail(self, organization_id: int, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM support_tickets WHERE id=? AND organization_id=?",
                (ticket_id, organization_id),
            ).fetchone()
            if not row:
                return None
            messages = [dict(message) for message in self._connection.execute(
                """SELECT id,author_id,author_kind,body,created_at
                   FROM support_messages WHERE ticket_id=? AND organization_id=?
                   ORDER BY created_at""",
                (ticket_id, organization_id),
            ).fetchall()]
        result = self._support_ticket(row)
        result["messages"] = messages
        return result

    def admin_support_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT organization_id FROM support_tickets WHERE id=?", (ticket_id,)
            ).fetchone()
        return self.support_ticket_detail(row["organization_id"], ticket_id) if row else None

    def reply_support_ticket(
        self,
        organization_id: int,
        ticket_id: str,
        actor_id: int,
        *,
        author_kind: str,
        message: str,
    ) -> dict[str, Any]:
        if author_kind not in {"customer", "support"}:
            raise SaaSError("Autor da mensagem inválido.", 422)
        body = message.strip()
        if len(body) < 2 or len(body) > 4000:
            raise SaaSError("A mensagem precisa ter entre 2 e 4.000 caracteres.", 422)
        message_id = str(uuid.uuid4())
        now = utc_now()
        with self._transaction():
            ticket = self._connection.execute(
                "SELECT * FROM support_tickets WHERE id=? AND organization_id=?",
                (ticket_id, organization_id),
            ).fetchone()
            if not ticket:
                raise SaaSError("Chamado não encontrado.", 404)
            status = ticket["status"]
            if author_kind == "customer" and status in {"resolved", "closed", "waiting_customer"}:
                status = "open"
            elif author_kind == "support" and status == "open":
                status = "in_progress"
            self._connection.execute(
                """INSERT INTO support_messages(
                     id,ticket_id,organization_id,author_id,author_kind,body,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (message_id, ticket_id, organization_id, actor_id, author_kind, body, now),
            )
            self._connection.execute(
                "UPDATE support_tickets SET status=?,updated_at=?,last_message_at=? WHERE id=?",
                (status, now, now, ticket_id),
            )
            if author_kind == "support":
                self._insert_notification(
                    organization_id,
                    ticket["opened_by"],
                    kind="support_reply",
                    title="Seu chamado recebeu uma resposta",
                    message=f"{ticket['subject'][:180]}",
                    action_tab=f"support:{ticket_id}",
                    deduplication_key=f"support-reply:{message_id}",
                )
        result = self.support_ticket_detail(organization_id, ticket_id)
        assert result is not None
        return result

    def admin_support_tickets(
        self,
        *,
        status: str | None = None,
        query: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        if status and status not in {"open", "in_progress", "waiting_customer", "resolved", "closed"}:
            raise SaaSError("Status de suporte inválido.", 422)
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("t.status=?")
            params.append(status)
        clean_query = query.strip().casefold()
        if clean_query:
            clauses.append("(lower(t.subject) LIKE ? OR lower(t.requester_identifier) LIKE ? OR t.id LIKE ?)")
            needle = f"%{clean_query}%"
            params.extend((needle, needle, needle))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT t.*,
                    (SELECT count(*) FROM support_messages m WHERE m.ticket_id=t.id) AS message_count,
                    (SELECT substr(body,1,180) FROM support_messages m WHERE m.ticket_id=t.id
                     ORDER BY m.created_at DESC LIMIT 1) AS last_message_preview
                    FROM support_tickets t {where}
                    ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                      WHEN 'normal' THEN 2 ELSE 3 END,
                      CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1
                      WHEN 'waiting_customer' THEN 2 WHEN 'resolved' THEN 3 ELSE 4 END,
                      t.updated_at DESC LIMIT ?""",
                (*params, limit),
            ).fetchall()
            counts = {
                row["status"]: row["total"]
                for row in self._connection.execute(
                    "SELECT status,count(*) AS total FROM support_tickets GROUP BY status"
                ).fetchall()
            }
        return {"tickets": [self._support_ticket(row) for row in rows], "counts": counts}

    def update_support_ticket(
        self,
        ticket_id: str,
        *,
        status: str,
        priority: str,
        actor_id: int,
    ) -> dict[str, Any]:
        if status not in {"open", "in_progress", "waiting_customer", "resolved", "closed"}:
            raise SaaSError("Status de suporte inválido.", 422)
        if priority not in {"low", "normal", "high", "urgent"}:
            raise SaaSError("Prioridade de suporte inválida.", 422)
        with self._transaction():
            cursor = self._connection.execute(
                """UPDATE support_tickets SET status=?,priority=?,assigned_to=?,updated_at=?
                   WHERE id=?""",
                (status, priority, actor_id, utc_now(), ticket_id),
            )
            if not cursor.rowcount:
                raise SaaSError("Chamado não encontrado.", 404)
        result = self.admin_support_ticket(ticket_id)
        assert result is not None
        return result

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
