"""Queued partner contact enrichment with a global, encrypted CPF cache.

Only the minimum contact payload is retained. Full CPFs are encrypted at rest
and are never returned by the HTTP API; a keyed digest provides cross-tenant
cache reuse without making the document searchable as plain text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def format_cnpj(value: Any) -> str:
    number = digits(value)
    if len(number) != 14:
        return str(value or "")
    return f"{number[:2]}.{number[2:5]}.{number[5:8]}/{number[8:12]}-{number[12:]}"


def format_cpf(value: Any) -> str:
    number = digits(value)
    if len(number) != 11:
        return str(value or "")
    return f"{number[:3]}.{number[3:6]}.{number[6:9]}-{number[9:]}"


def mask_cpf(value: Any) -> str:
    number = digits(value)
    if len(number) != 11:
        return "***.***.***-**"
    return f"***.{number[3:6]}.{number[6:9]}-**"


LOWERCASE_NAME_PARTS = {"da", "das", "de", "do", "dos", "e"}
FREE_EMAIL_DOMAINS = {
    "aol.com", "bol.com.br", "gmail.com", "googlemail.com", "hotmail.com",
    "hotmail.com.br", "icloud.com", "live.com", "live.com.br", "mail.com",
    "msn.com", "outlook.com", "outlook.com.br", "proton.me", "protonmail.com",
    "terra.com.br", "uol.com.br", "yahoo.com", "yahoo.com.br", "ymail.com",
}


def title_name(value: Any) -> str | None:
    words = " ".join(str(value or "").split()).lower().split(" ")
    if not words or not words[0]:
        return None
    return " ".join(
        word if index and word in LOWERCASE_NAME_PARTS
        else "-".join(part.capitalize() for part in word.split("-"))
        for index, word in enumerate(words)
    )


def valid_cpf(value: Any) -> bool:
    number = digits(value)
    if len(number) != 11 or number == number[0] * 11:
        return False
    for length in (9, 10):
        total = sum(int(number[index]) * (length + 1 - index) for index in range(length))
        check = (total * 10 % 11) % 10
        if check != int(number[length]):
            return False
    return True


def _normalized_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).upper().split())


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _payload_root(payload: Any) -> dict[str, Any]:
    current = payload
    for _ in range(3):
        if not isinstance(current, dict):
            return {}
        nested = next((
            current.get(key)
            for key in ("data", "resultado", "result", "pessoa", "empresa")
            if isinstance(current.get(key), dict)
        ), None)
        if nested is None:
            return current
        current = nested
    return current if isinstance(current, dict) else {}


class LemitError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GlobalRateLimiter:
    def __init__(self, requests_per_second: float):
        self.interval = 1.0 / max(0.1, float(requests_per_second))
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay:
            time.sleep(delay)


class LemitClient:
    BASE_URL = "https://api.lemit.com.br/api/v1"

    def __init__(
        self,
        token: str,
        *,
        segment: int = 20782,
        requests_per_second: float = 10.0,
        timeout: float = 15.0,
        max_retries: int = 3,
    ):
        self.token = token.strip()
        self.segment = int(segment)
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.rate_limiter = GlobalRateLimiter(requests_per_second)

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _post(self, endpoint: str, document: str | None = None) -> dict[str, Any]:
        if not self.configured:
            raise LemitError("Integração Lemit ainda não configurada.")
        fields = {"segmento": str(self.segment)}
        if document is not None:
            fields["documento"] = digits(document)
        body = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(
            f"{self.BASE_URL}/{endpoint.lstrip('/')}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "EchoPJs/partner-enrichment",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    parsed = json.loads(raw.decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise LemitError("Resposta inesperada da Lemit.", status_code=response.status)
                    return parsed
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code == 429 and attempt < self.max_retries:
                    retry_after = error.headers.get("Retry-After", "")
                    try:
                        delay = min(30.0, max(0.25, float(retry_after)))
                    except ValueError:
                        delay = min(8.0, 0.5 * (2 ** attempt))
                    time.sleep(delay)
                    continue
                if 500 <= error.code < 600 and attempt < self.max_retries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
                    continue
                raise LemitError("Falha na consulta à Lemit.", status_code=error.code) from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < self.max_retries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
                    continue
                break
        raise LemitError("Lemit temporariamente indisponível.") from last_error

    def company(self, cnpj: str) -> dict[str, Any]:
        return self._post("consulta/empresa", cnpj)

    def person(self, cpf: str) -> dict[str, Any]:
        return self._post("consulta/pessoa", cpf)

    def balance(self) -> dict[str, Any]:
        return self._post("saldo")


class PartnerEnrichmentStore:
    def __init__(self, database_path: str, encryption_secret: str, *, cache_days: int = 60):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        material = (encryption_secret or "unconfigured-enrichment-key").encode("utf-8")
        self._hash_key = hashlib.sha256(b"hash:" + material).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"cipher:" + material).digest()))
        self.cache_days = max(1, int(cache_days))
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS partner_enrichment_jobs (
              id TEXT PRIMARY KEY,
              organization_id INTEGER NOT NULL,
              list_id TEXT NOT NULL,
              created_by INTEGER NOT NULL,
              status TEXT NOT NULL,
              total_companies INTEGER NOT NULL,
              processed_companies INTEGER NOT NULL DEFAULT 0,
              companies_succeeded INTEGER NOT NULL DEFAULT 0,
              companies_failed INTEGER NOT NULL DEFAULT 0,
              selected_people INTEGER NOT NULL DEFAULT 0,
              provider_requests INTEGER NOT NULL DEFAULT 0,
              reused_people INTEGER NOT NULL DEFAULT 0,
              contacts_found INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_partner_jobs_status_created
              ON partner_enrichment_jobs(status,created_at);
            CREATE INDEX IF NOT EXISTS idx_partner_jobs_org_list
              ON partner_enrichment_jobs(organization_id,list_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS partner_enrichment_job_items (
              job_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              cnpj TEXT NOT NULL,
              company_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              result_json TEXT,
              error TEXT,
              PRIMARY KEY(job_id,position),
              FOREIGN KEY(job_id) REFERENCES partner_enrichment_jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_partner_items_pending
              ON partner_enrichment_job_items(job_id,status,position);
            CREATE TABLE IF NOT EXISTS partner_person_cache (
              cpf_hash TEXT PRIMARY KEY,
              cpf_ciphertext TEXT NOT NULL,
              cpf_masked TEXT NOT NULL,
              person_json TEXT NOT NULL,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS partner_enrichment_access (
              organization_id INTEGER NOT NULL,
              cnpj TEXT NOT NULL,
              cpf_hash TEXT NOT NULL,
              partner_json TEXT NOT NULL,
              job_id TEXT NOT NULL,
              linked_at TEXT NOT NULL,
              PRIMARY KEY(organization_id,cnpj,cpf_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_partner_access_org_cnpj
              ON partner_enrichment_access(organization_id,cnpj,linked_at DESC);
            CREATE TABLE IF NOT EXISTS lemit_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              organization_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              endpoint TEXT NOT NULL,
              segment INTEGER NOT NULL,
              cache_hit INTEGER NOT NULL,
              status_code INTEGER,
              duration_ms INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lemit_usage_org_created
              ON lemit_usage(organization_id,created_at);
        """)
        self._connection.execute("UPDATE partner_enrichment_jobs SET status='queued' WHERE status='running'")
        self._connection.execute("UPDATE partner_enrichment_job_items SET status='queued' WHERE status='running'")
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def health_check(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1").fetchone()[0] == 1

    def _cpf_hash(self, cpf: str) -> str:
        return hmac.new(self._hash_key, digits(cpf).encode("ascii"), hashlib.sha256).hexdigest()

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        total = job["total_companies"]
        job["progress_percent"] = round(100 * job["processed_companies"] / total, 1) if total else 100.0
        return job

    def create_job(
        self,
        organization_id: int,
        list_id: str,
        user_id: int,
        companies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock, self._connection:
            active = self._connection.execute(
                """SELECT * FROM partner_enrichment_jobs
                   WHERE organization_id=? AND list_id=? AND status IN ('queued','running')
                   ORDER BY created_at DESC LIMIT 1""",
                (organization_id, list_id),
            ).fetchone()
            if active:
                return self._job(active)
            job_id = str(uuid.uuid4())
            now = utc_now()
            self._connection.execute(
                """INSERT INTO partner_enrichment_jobs(
                     id,organization_id,list_id,created_by,status,total_companies,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (job_id, organization_id, list_id, user_id, "queued", len(companies), now),
            )
            self._connection.executemany(
                """INSERT INTO partner_enrichment_job_items(
                     job_id,position,cnpj,company_json
                   ) VALUES(?,?,?,?)""",
                [
                    (job_id, index, digits(company.get("cnpj")), json.dumps(company, ensure_ascii=False))
                    for index, company in enumerate(companies)
                ],
            )
        return self.get_job(job_id, organization_id=organization_id)  # type: ignore[return-value]

    def get_job(self, job_id: str, *, organization_id: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM partner_enrichment_jobs WHERE id=?", (job_id,),
            ).fetchone()
        if not row or (organization_id is not None and row["organization_id"] != organization_id):
            return None
        return self._job(row)

    def latest_job(self, organization_id: int, list_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM partner_enrichment_jobs
                   WHERE organization_id=? AND list_id=? ORDER BY created_at DESC LIMIT 1""",
                (organization_id, list_id),
            ).fetchone()
        return self._job(row) if row else None

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT * FROM partner_enrichment_jobs
                   WHERE status IN ('running','queued')
                   ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,created_at LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            if row["status"] == "queued":
                self._connection.execute(
                    "UPDATE partner_enrichment_jobs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?",
                    (utc_now(), row["id"]),
                )
            updated = self._connection.execute(
                "SELECT * FROM partner_enrichment_jobs WHERE id=?", (row["id"],),
            ).fetchone()
        return self._job(updated)

    def next_item(self, job_id: str) -> tuple[int, dict[str, Any]] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT position,company_json FROM partner_enrichment_job_items
                   WHERE job_id=? AND status='queued' ORDER BY position LIMIT 1""",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                "UPDATE partner_enrichment_job_items SET status='running' WHERE job_id=? AND position=?",
                (job_id, row["position"]),
            )
        return row["position"], json.loads(row["company_json"])

    def complete_item(self, job_id: str, position: int, result: dict[str, Any]) -> None:
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE partner_enrichment_job_items
                   SET status='completed',result_json=?,error=NULL
                   WHERE job_id=? AND position=? AND status='running'""",
                (json.dumps(result, ensure_ascii=False), job_id, position),
            ).rowcount
            if changed:
                self._connection.execute(
                    """UPDATE partner_enrichment_jobs SET
                         processed_companies=processed_companies+1,
                         companies_succeeded=companies_succeeded+1,
                         selected_people=selected_people+?,provider_requests=provider_requests+?,
                         reused_people=reused_people+?,contacts_found=contacts_found+?
                       WHERE id=?""",
                    (
                        result.get("selected_people", 0), result.get("provider_requests", 0),
                        result.get("reused_people", 0), result.get("contacts_found", 0), job_id,
                    ),
                )

    def fail_item(self, job_id: str, position: int, error: Exception, *, provider_requests: int = 0) -> None:
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE partner_enrichment_job_items SET status='failed',error=?
                   WHERE job_id=? AND position=? AND status='running'""",
                (type(error).__name__, job_id, position),
            ).rowcount
            if changed:
                self._connection.execute(
                    """UPDATE partner_enrichment_jobs SET processed_companies=processed_companies+1,
                         companies_failed=companies_failed+1,provider_requests=provider_requests+?
                       WHERE id=?""",
                    (provider_requests, job_id),
                )

    def finish_if_complete(self, job_id: str) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT total_companies,processed_companies,companies_failed FROM partner_enrichment_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if not row or row["processed_companies"] < row["total_companies"]:
                return False
            status = "completed_with_errors" if row["companies_failed"] else "completed"
            self._connection.execute(
                "UPDATE partner_enrichment_jobs SET status=?,finished_at=COALESCE(finished_at,?) WHERE id=?",
                (status, utc_now(), job_id),
            )
            return True

    def cached_person(self, cpf: str) -> dict[str, Any] | None:
        cpf_hash = self._cpf_hash(cpf)
        with self._lock:
            row = self._connection.execute(
                "SELECT person_json,fetched_at FROM partner_person_cache WHERE cpf_hash=?", (cpf_hash,),
            ).fetchone()
        if not row:
            return None
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
        except ValueError:
            return None
        if fetched < datetime.now(timezone.utc) - timedelta(days=self.cache_days):
            return None
        return {**json.loads(row["person_json"]), "fetched_at": row["fetched_at"]}

    def save_person(self, cpf: str, person: dict[str, Any]) -> str:
        number = digits(cpf)
        cpf_hash = self._cpf_hash(number)
        encrypted = self._fernet.encrypt(format_cpf(number).encode("ascii")).decode("ascii")
        fetched_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO partner_person_cache(cpf_hash,cpf_ciphertext,cpf_masked,person_json,fetched_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(cpf_hash) DO UPDATE SET
                   cpf_ciphertext=excluded.cpf_ciphertext,cpf_masked=excluded.cpf_masked,
                   person_json=excluded.person_json,fetched_at=excluded.fetched_at""",
                (cpf_hash, encrypted, mask_cpf(number), json.dumps(person, ensure_ascii=False), fetched_at),
            )
        return fetched_at

    def link_partner(
        self,
        organization_id: int,
        cnpj: str,
        cpf: str,
        partner: dict[str, Any],
        job_id: str,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO partner_enrichment_access(
                     organization_id,cnpj,cpf_hash,partner_json,job_id,linked_at
                   ) VALUES(?,?,?,?,?,?) ON CONFLICT(organization_id,cnpj,cpf_hash) DO UPDATE SET
                   partner_json=excluded.partner_json,job_id=excluded.job_id,linked_at=excluded.linked_at""",
                (
                    organization_id, digits(cnpj), self._cpf_hash(cpf),
                    json.dumps(partner, ensure_ascii=False), job_id, utc_now(),
                ),
            )

    def contacts_for_companies(self, organization_id: int, cnpjs: list[str]) -> dict[str, list[dict[str, Any]]]:
        clean = list(dict.fromkeys(digits(cnpj) for cnpj in cnpjs if digits(cnpj)))
        if not clean:
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        with self._lock:
            for start in range(0, len(clean), 400):
                chunk = clean[start:start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._connection.execute(
                    f"""SELECT cnpj,partner_json FROM partner_enrichment_access
                        WHERE organization_id=? AND cnpj IN ({placeholders})
                        ORDER BY cnpj,linked_at DESC""",
                    (organization_id, *chunk),
                ).fetchall()
                for row in rows:
                    result.setdefault(row["cnpj"], []).append(json.loads(row["partner_json"]))
        return result

    def record_usage(
        self,
        *,
        job_id: str,
        organization_id: int,
        user_id: int,
        endpoint: str,
        segment: int,
        cache_hit: bool,
        status_code: int | None,
        duration_ms: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO lemit_usage(
                     job_id,organization_id,user_id,endpoint,segment,cache_hit,status_code,duration_ms,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, organization_id, user_id, endpoint, segment, int(cache_hit),
                    status_code, max(0, int(duration_ms)), utc_now(),
                ),
            )


def _rank_value(item: dict[str, Any]) -> float:
    for key in ("ranking", "rank", "score", "confianca", "confidence"):
        value = item.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 999999.0


def _phone_values(
    payload: dict[str, Any],
    source_keys: tuple[str, ...] = ("celulares", "telefones_moveis", "mobiles"),
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    root = _payload_root(payload)
    values: list[Any] = []
    for source_key in source_keys:
        if isinstance(root.get(source_key), list):
            values = root[source_key]
            break
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in sorted((item for item in values if isinstance(item, dict)), key=_rank_value):
        area = digits(raw.get("ddd") or raw.get("area") or raw.get("codigo_area"))
        number = digits(raw.get("numero") or raw.get("telefone") or raw.get("phone"))
        full = digits(raw.get("celular") or raw.get("mobile"))
        if full and not number:
            if len(full) in (12, 13) and full.startswith("55"):
                full = full[2:]
            area, number = (full[:2], full[2:]) if len(full) in (10, 11) else ("", full)
        if not area and len(number) in (10, 11):
            area, number = number[:2], number[2:]
        key = f"{area}{number}"
        if len(key) not in (10, 11) or key in seen:
            continue
        seen.add(key)
        result.append({
            "value": f"55{key}",
            "ddd": area,
            "numero": number,
            "whatsapp": bool(raw.get("whatsapp") or raw.get("possui_whatsapp")),
            "plus": bool(raw.get("plus")),
            "ranking": None if _rank_value(raw) == 999999.0 else _rank_value(raw),
        })
        if len(result) == limit:
            break
    return result


def _email_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = _payload_root(payload)
    values = _as_list(root.get("emails") or root.get("enderecos_email"))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in sorted((item for item in values if isinstance(item, dict)), key=_rank_value):
        email = str(raw.get("email") or raw.get("endereco") or "").strip().lower()
        if "@" not in email or email in seen:
            continue
        seen.add(email)
        domain = email.rsplit("@", 1)[1]
        result.append({
            "email": email,
            "domain": domain,
            "corporate": domain not in FREE_EMAIL_DOMAINS,
            "possui_cookie": bool(raw.get("possui_cookie") or raw.get("cookie")),
            "ranking": None if _rank_value(raw) == 999999.0 else _rank_value(raw),
        })
        if len(result) == 3:
            break
    return result


def minimal_person(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    deceased = bool(root.get("falecido") or root.get("obito") or root.get("deceased"))
    name = title_name(root.get("nome") or root.get("name"))
    emails = [] if deceased else _email_values(payload)
    return {
        "first_name": name.split(" ", 1)[0] if name else None,
        "name": name,
        "deceased": deceased,
        "phones": [] if deceased else _phone_values(payload),
        "fixed_phones": [] if deceased else _phone_values(
            payload, ("fixos", "telefones_fixos", "landlines"),
        ),
        "emails": emails,
        "corporate_emails": [email for email in emails if email["corporate"]],
    }


def _company_partners(payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = _payload_root(payload)
    company = root.get("empresa") if isinstance(root.get("empresa"), dict) else root
    values = company.get("socios") or company.get("quadro_societario") or company.get("qsa") or []
    return [item for item in _as_list(values) if isinstance(item, dict)]


def _local_partner(provider: dict[str, Any], local_partners: list[dict[str, Any]]) -> dict[str, Any]:
    provider_name = _normalized_text(provider.get("nome") or provider.get("name"))
    if provider_name:
        match = next(
            (item for item in local_partners if _normalized_text(item.get("partner_name")) == provider_name),
            None,
        )
        if match:
            return match
    cpf = digits(provider.get("cpf") or provider.get("documento") or provider.get("document"))
    middle = cpf[3:9] if len(cpf) == 11 else ""
    if middle:
        match = next(
            (item for item in local_partners if middle in digits(item.get("partner_document"))),
            None,
        )
        if match:
            return match
    return {}


def prioritized_partners(
    provider_partners: list[dict[str, Any]],
    local_partners: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    seen: set[str] = set()
    for provider in provider_partners:
        cpf = digits(provider.get("cpf") or provider.get("documento") or provider.get("document"))
        if not valid_cpf(cpf) or cpf in seen:
            continue
        seen.add(cpf)
        local = _local_partner(provider, local_partners)
        partner_type = _normalized_text(local.get("partner_type") or local.get("partner_type_code"))
        if "JURID" in partner_type or "ESTRANGEIR" in partner_type:
            continue
        age_code_text = digits(local.get("age_range_code"))
        age_code = int(age_code_text) if age_code_text else None
        if age_code in (1, 2):
            continue
        qualification = local.get("qualification") or local.get("qualification_code") or provider.get("qualificacao") or ""
        role = _normalized_text(qualification)
        if any(word in role for word in ("ADMINISTRADOR", "TITULAR", "PRESIDENTE", "DIRETOR")):
            role_rank = 0
        elif any(word in role for word in ("SOCIO", "CONSELHEIRO")):
            role_rank = 1
        else:
            role_rank = 2
        age_group = 0 if age_code in (3, 4, 5, 6, 7) else 2 if age_code in (8, 9) else 1
        age_rank = {4: 0, 5: 0, 6: 1, 3: 2, 7: 3, None: 4, 8: 5, 9: 6}.get(age_code, 4)
        participation = provider.get("participacao") or provider.get("participation") or 0
        try:
            participation_rank = -float(str(participation).replace(",", "."))
        except (TypeError, ValueError):
            participation_rank = 0.0
        merged = {
            "cpf": cpf,
            "name": provider.get("nome") or provider.get("name") or local.get("partner_name"),
            "qualification": qualification or None,
            "age_range": local.get("age_range"),
            "age_range_code": age_code,
            "participation": provider.get("participacao") or provider.get("participation"),
        }
        candidates.append(((age_group, role_rank, age_rank, participation_rank, _normalized_text(merged["name"])), merged))
    candidates.sort(key=lambda item: item[0])
    return [item[1] for item in candidates[:max(0, limit)]]


class PartnerEnrichmentService:
    def __init__(
        self,
        store: PartnerEnrichmentStore,
        client: LemitClient,
        company_detail: Callable[[str], dict[str, Any] | None],
    ):
        self.store = store
        self.client = client
        self.company_detail = company_detail

    def _provider_call(
        self,
        endpoint: str,
        document: str,
        *,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()
        status_code: int | None = 200
        try:
            return self.client.company(document) if endpoint == "empresa" else self.client.person(document)
        except LemitError as error:
            status_code = error.status_code
            raise
        finally:
            self.store.record_usage(
                job_id=job["id"], organization_id=job["organization_id"],
                user_id=job["created_by"], endpoint=endpoint, segment=self.client.segment,
                cache_hit=False, status_code=status_code,
                duration_ms=round((time.monotonic() - started) * 1000),
            )

    def process_company(self, company: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        cnpj = digits(company.get("cnpj"))
        provider_requests = 0
        company_payload = self._provider_call("empresa", cnpj, job=job)
        provider_requests += 1
        local_detail = self.company_detail(cnpj) or {}
        local_partners = local_detail.get("partners") or company.get("partners") or []
        selected = prioritized_partners(_company_partners(company_payload), local_partners, limit=3)
        reused = 0
        contacts_found = 0
        partners: list[dict[str, Any]] = []
        for candidate in selected:
            cpf = candidate.pop("cpf")
            person = self.store.cached_person(cpf)
            if person is None:
                person_payload = self._provider_call("pessoa", cpf, job=job)
                provider_requests += 1
                person = minimal_person(person_payload)
                person["fetched_at"] = self.store.save_person(cpf, person)
            else:
                reused += 1
                self.store.record_usage(
                    job_id=job["id"], organization_id=job["organization_id"],
                    user_id=job["created_by"], endpoint="pessoa", segment=self.client.segment,
                    cache_hit=True, status_code=None, duration_ms=0,
                )
            partner = {
                **candidate,
                "first_name": person.get("first_name") or (title_name(candidate.get("name")) or "").split(" ", 1)[0] or None,
                "name": person.get("name") or title_name(candidate.get("name")),
                "cpf_masked": mask_cpf(cpf),
                "phones": person.get("phones") or [],
                "fixed_phones": person.get("fixed_phones") or [],
                "emails": person.get("emails") or [],
                "corporate_emails": person.get("corporate_emails") or [],
                "deceased": bool(person.get("deceased")),
                "fetched_at": person.get("fetched_at"),
            }
            contacts_found += len(partner["phones"]) + len(partner["fixed_phones"]) + len(partner["emails"])
            self.store.link_partner(job["organization_id"], cnpj, cpf, partner, job["id"])
            partners.append(partner)
        return {
            "cnpj": format_cnpj(cnpj),
            "selected_people": len(selected),
            "provider_requests": provider_requests,
            "reused_people": reused,
            "contacts_found": contacts_found,
            "partners": partners,
        }


class PartnerEnrichmentRunner:
    def __init__(self, store: PartnerEnrichmentStore, service: PartnerEnrichmentService):
        self.store = store
        self.service = service
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="partner-enrichment-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=15)

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next_job()
            if not job:
                self._wake.wait(timeout=2)
                self._wake.clear()
                continue
            pending = self.store.next_item(job["id"])
            if not pending:
                self.store.finish_if_complete(job["id"])
                continue
            position, company = pending
            try:
                result = self.service.process_company(company, job)
                self.store.complete_item(job["id"], position, result)
            except Exception as error:
                self.store.fail_item(job["id"], position, error)
            self.store.finish_if_complete(job["id"])
