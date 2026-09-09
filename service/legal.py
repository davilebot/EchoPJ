"""Versioned public legal-document configuration for the SaaS signup flow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
_EMAIL_PATTERN = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True)
class LegalDocuments:
    operator_name: str
    operator_document: str
    operator_address: str
    contact_email: str
    privacy_email: str
    terms_version: str
    privacy_version: str
    effective_date: str
    retention_policy: str

    @classmethod
    def from_settings(cls, settings: Any) -> "LegalDocuments":
        return cls(
            operator_name=_clean(settings.legal_operator_name, 160),
            operator_document=_clean(settings.legal_operator_document, 40),
            operator_address=_clean(settings.legal_operator_address, 300),
            contact_email=_clean(settings.legal_contact_email, 254).casefold(),
            privacy_email=_clean(settings.legal_privacy_email, 254).casefold(),
            terms_version=_clean(settings.legal_terms_version, 40),
            privacy_version=_clean(settings.legal_privacy_version, 40),
            effective_date=_clean(settings.legal_effective_date, 10),
            retention_policy=_clean(settings.legal_retention_policy, 600),
        )

    @property
    def configured(self) -> bool:
        try:
            date.fromisoformat(self.effective_date)
        except ValueError:
            return False
        return bool(
            self.operator_name
            and self.operator_document
            and self.operator_address
            and _EMAIL_PATTERN.fullmatch(self.contact_email)
            and _EMAIL_PATTERN.fullmatch(self.privacy_email)
            and _VERSION_PATTERN.fullmatch(self.terms_version)
            and _VERSION_PATTERN.fullmatch(self.privacy_version)
            and self.retention_policy
        )

    def accepts(self, terms_version: str, privacy_version: str) -> bool:
        return bool(
            self.configured
            and terms_version == self.terms_version
            and privacy_version == self.privacy_version
        )

    def public(self) -> dict[str, Any]:
        company = {
            "name": self.operator_name or None,
            "document": self.operator_document or None,
            "address": self.operator_address or None,
            "contact_email": self.contact_email or None,
            "privacy_email": self.privacy_email or None,
        }
        return {
            "configured": self.configured,
            "status": "published" if self.configured else "draft",
            "company": company,
            "effective_date": self.effective_date or None,
            "retention_policy": self.retention_policy or None,
            "documents": {
                "terms": {
                    "title": "Termos de Uso",
                    "version": self.terms_version or None,
                    "url": "/termos",
                },
                "privacy": {
                    "title": "Política de Privacidade",
                    "version": self.privacy_version or None,
                    "url": "/privacidade",
                },
            },
        }
