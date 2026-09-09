"""Idempotent customer billing email delivery."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

from .mail import send_billing_alert


class BillingEmailDispatcher:
    def __init__(
        self,
        settings,
        saas_store,
        auth_store,
        *,
        enabled: bool,
        interval_seconds: int = 6 * 60 * 60,
        sender: Callable = send_billing_alert,
    ):
        self.settings = settings
        self.saas_store = saas_store
        self.auth_store = auth_store
        self.enabled = bool(enabled)
        self.interval_seconds = max(60, int(interval_seconds))
        self.sender = sender
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_at: str | None = None
        self._last_error_at: str | None = None
        self._last_result: dict[str, int | bool] | None = None

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="billing-email-dispatcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._last_result = self.run_once()
                self._last_run_at = datetime.now(timezone.utc).isoformat()
            except Exception:
                # The next cycle retries safely; never terminate the application for email.
                self._last_error_at = datetime.now(timezone.utc).isoformat()
            self._stop_event.wait(self.interval_seconds)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "interval_minutes": self.interval_seconds // 60,
            "last_run_at": self._last_run_at,
            "last_error_at": self._last_error_at,
            "last_result": self._last_result,
        }

    def run_once(self, *, now: datetime | None = None) -> dict[str, int | bool]:
        if not self.enabled:
            return {"enabled": False, "candidates": 0, "sent": 0, "failed": 0}
        current = now or datetime.now(timezone.utc)
        candidates = self.saas_store.billing_email_candidates(now=current)
        result = {"enabled": True, "candidates": len(candidates), "sent": 0, "failed": 0}
        for candidate in candidates:
            contacts = self.auth_store.organization_administrator_contacts(
                candidate["organization_id"]
            )
            for contact in contacts:
                claimed = self.saas_store.claim_billing_email_delivery(
                    candidate["organization_id"],
                    contact["user_id"],
                    recipient=contact["email"],
                    kind=candidate["kind"],
                    cycle_key=candidate["cycle_key"],
                    plan_name=candidate["plan_name"],
                    due_date=candidate["due_date"],
                    now=current,
                )
                if not claimed:
                    continue
                link = (
                    f"{self.settings.app_public_url.rstrip('/')}"
                    f"/?organization={candidate['organization_id']}&tab=billing"
                )
                try:
                    delivery = self.sender(
                        self.settings,
                        email=contact["email"],
                        organization_name=contact["organization_name"],
                        kind=candidate["kind"],
                        plan_name=candidate["plan_name"],
                        due_date=candidate["due_date"] or "data não informada",
                        link=link,
                    )
                except Exception:
                    delivery = "failed"
                sent = delivery == "sent"
                self.saas_store.complete_billing_email_delivery(
                    candidate["organization_id"],
                    contact["user_id"],
                    kind=candidate["kind"],
                    cycle_key=candidate["cycle_key"],
                    sent=sent,
                    now=current,
                )
                result["sent" if sent else "failed"] += 1
        return result
