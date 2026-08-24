from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from time import monotonic
from typing import Any

from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from plataforma_receita.matcher import decide
from plataforma_receita.normalization import digits, normalize

from .search import SearchCapabilities, build_search_query
from .explorer import FIELD_GROUPS


class Repository:
    def __init__(self, dsn: str, *, database_workers: int = 8, statement_timeout_ms: int = 1800):
        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max(2, database_workers),
            kwargs={"row_factory": dict_row},
            open=False,
        )
        self.database_workers = database_workers
        self.statement_timeout_ms = statement_timeout_ms

    def open(self) -> None:
        self.pool.open(wait=True)

    def close(self) -> None:
        self.pool.close()

    def current_version(self) -> str | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT version FROM dataset_versions WHERE is_current AND status='ready' ORDER BY imported_at DESC LIMIT 1"
            ).fetchone()
            return row["version"] if row else None

    def search_capabilities(self) -> SearchCapabilities:
        with self.pool.connection() as connection:
            row = connection.execute("""
                SELECT
                  to_regclass('public.rfb_current_simples') IS NOT NULL AS simples,
                  to_regclass('public.rfb_current_company_details') IS NOT NULL AS company_details,
                  to_regclass('public.rfb_current_establishment_details') IS NOT NULL AS establishment_details,
                  to_regclass('public.rfb_aux_datasets') IS NOT NULL AS datasets
            """).fetchone()
            compatible_current = False
            if row["datasets"]:
                auxiliary = connection.execute(
                    "SELECT version FROM rfb_aux_datasets WHERE status='current' LIMIT 1"
                ).fetchone()
                base = connection.execute(
                    "SELECT version FROM dataset_versions WHERE is_current AND status='ready' LIMIT 1"
                ).fetchone()
                compatible_current = bool(auxiliary and base and auxiliary["version"] == base["version"])
        return SearchCapabilities(
            simples=bool(row["simples"] and compatible_current),
            company_details=bool(row["company_details"] and compatible_current),
            establishment_details=bool(row["establishment_details"] and compatible_current),
        )

    @staticmethod
    def _serializable(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    @classmethod
    def _candidate(cls, row: dict[str, Any]) -> dict[str, Any]:
        address = " ".join(filter(None, [
            row.get("street_type"), row.get("street"), row.get("street_number"),
            row.get("address_extra"), row.get("district"),
        ]))
        fields = {
            "cnpj": row["cnpj"],
            "cnpj_root": row["cnpj_root"],
            "legal_name": row.get("legal_name"),
            "trade_name": row.get("trade_name"),
            "registration_status": row.get("registration_status"),
            "registration_status_date": row.get("registration_status_date"),
            "opened_at": row.get("opened_at"),
            "company_size": row.get("company_size"),
            "share_capital": row.get("share_capital"),
            "primary_cnae": row.get("primary_cnae"),
            "secondary_cnaes": row.get("secondary_cnaes") or [],
            "municipality": row.get("municipality"),
            "uf": row.get("uf"),
            "postal_code": row.get("postal_code"),
            "address": address,
            "dataset_version": row.get("dataset_version"),
        }
        return {key: cls._serializable(value) for key, value in fields.items()}

    @classmethod
    def _search_result(cls, row: dict[str, Any]) -> dict[str, Any]:
        address = " ".join(filter(None, [
            row.get("street_type"), row.get("street"), row.get("street_number"),
            row.get("address_extra"), row.get("district"),
        ]))
        fields = {
            "cnpj": row["cnpj"],
            "legal_name": row.get("legal_name"),
            "trade_name": row.get("trade_name"),
            "registration_status": row.get("registration_status"),
            "registration_status_date": row.get("registration_status_date"),
            "opened_at": row.get("opened_at"),
            "company_size": row.get("company_size"),
            "share_capital": row.get("share_capital"),
            "primary_cnae": row.get("primary_cnae"),
            "secondary_cnaes": row.get("secondary_cnaes") or [],
            "municipality": row.get("municipality"),
            "uf": row.get("uf"),
            "postal_code": row.get("postal_code"),
            "address": address,
            "is_simples": row.get("is_simples"),
            "is_mei": row.get("is_mei"),
            "legal_nature_code": row.get("legal_nature_code"),
            "branch_type_code": row.get("branch_type_code"),
            "email": row.get("email"),
            "phone_area_code": row.get("phone1_area_code"),
            "phone": row.get("phone1"),
            "dataset_version": row.get("dataset_version"),
        }
        return {key: cls._serializable(value) for key, value in fields.items()}

    def search_companies(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], SearchCapabilities, int, bool]:
        started = monotonic()
        capabilities = self.search_capabilities()
        sql, parameters = build_search_query(filters, capabilities)
        with self.pool.connection() as connection:
            connection.execute(
                "SELECT set_config('statement_timeout',%s,true)",
                (str(max(15_000, self.statement_timeout_ms * 5)),),
            )
            rows = connection.execute(sql, parameters).fetchall()
        limit = int(filters["limit"])
        has_more = len(rows) > limit
        rows = rows[:limit]
        duration_ms = round((monotonic() - started) * 1000)
        return [self._search_result(row) for row in rows], capabilities, duration_ms, has_more

    def explorer_overview(self) -> dict[str, Any]:
        with self.pool.connection() as connection:
            base = connection.execute("""
                SELECT version,imported_at,active_count,inactive_count
                FROM dataset_versions
                WHERE is_current AND status='ready'
                ORDER BY imported_at DESC LIMIT 1
            """).fetchone()
            database_size = connection.execute(
                "SELECT pg_database_size(current_database()) AS bytes"
            ).fetchone()["bytes"]
            auxiliary = None
            progress: list[dict[str, Any]] = []
            if connection.execute(
                "SELECT to_regclass('public.rfb_aux_datasets') IS NOT NULL AS available"
            ).fetchone()["available"]:
                auxiliary = connection.execute("""
                    SELECT version,status,started_at,published_at,completed_at,error,metadata
                    FROM rfb_aux_datasets
                    ORDER BY started_at DESC LIMIT 1
                """).fetchone()
                if auxiliary:
                    progress = connection.execute("""
                        SELECT kind,
                               count(*) AS files,
                               count(*) FILTER (WHERE status='completed') AS completed_files,
                               coalesce(sum(rows_loaded),0) AS rows_loaded,
                               bool_or(status='running') AS running,
                               bool_or(status='failed') AS failed
                        FROM rfb_aux_import_files
                        WHERE dataset_version=%s
                        GROUP BY kind ORDER BY kind
                    """, (auxiliary["version"],)).fetchall()
        active = int(base["active_count"]) if base else 0
        inactive = int(base["inactive_count"]) if base else 0
        return {
            "dataset_version": base["version"] if base else None,
            "imported_at": self._serializable(base["imported_at"]) if base else None,
            "active_establishments": active,
            "inactive_establishments": inactive,
            "total_establishments": active + inactive,
            "database_bytes": int(database_size),
            "auxiliary": {
                key: self._serializable(value)
                for key, value in dict(auxiliary).items()
            } if auxiliary else None,
            "progress": [
                {key: self._serializable(value) for key, value in dict(row).items()}
                for row in progress
            ],
            "field_groups": FIELD_GROUPS,
        }

    @classmethod
    def _company_detail_core(cls, row: dict[str, Any]) -> dict[str, Any]:
        fields = dict(row)
        fields["address"] = " ".join(filter(None, [
            row.get("street_type"), row.get("street"), row.get("street_number"),
            row.get("address_extra"), row.get("district"),
        ]))
        return {key: cls._serializable(value) for key, value in fields.items()}

    def company_detail(self, cnpj: str) -> dict[str, Any] | None:
        capabilities = self.search_capabilities()
        with self.pool.connection() as connection:
            core = connection.execute(
                "SELECT * FROM rfb_establishments WHERE cnpj=%s LIMIT 1", (cnpj,)
            ).fetchone()
            if not core:
                return None
            company = establishment = simples = None
            partners: list[dict[str, Any]] = []
            references: dict[str, str] = {}
            if capabilities.company_details:
                company = connection.execute(
                    "SELECT * FROM rfb_current_company_details WHERE cnpj_root=%s",
                    (core["cnpj_root"],),
                ).fetchone()
            if capabilities.establishment_details:
                establishment = connection.execute(
                    "SELECT * FROM rfb_current_establishment_details WHERE cnpj=%s",
                    (cnpj,),
                ).fetchone()
            if capabilities.simples:
                simples = connection.execute(
                    "SELECT * FROM rfb_current_simples WHERE cnpj_root=%s",
                    (core["cnpj_root"],),
                ).fetchone()
                partners = connection.execute("""
                    SELECT p.dataset_version,p.cnpj_root,p.partner_type_code,p.partner_type,
                           p.partner_name,p.partner_document,p.qualification_code,p.joined_at,
                           p.country_code,p.legal_representative_document,
                           p.legal_representative_name,p.legal_representative_qualification_code,
                           p.age_range_code,p.age_range,
                           q.label AS qualification,
                           co.label AS country,
                           rq.label AS legal_representative_qualification
                    FROM rfb_current_partners p
                    LEFT JOIN rfb_current_aux_reference q
                      ON q.kind='qualification' AND q.code=p.qualification_code
                    LEFT JOIN rfb_current_aux_reference co
                      ON co.kind='country' AND co.code=p.country_code
                    LEFT JOIN rfb_current_aux_reference rq
                      ON rq.kind='qualification' AND rq.code=p.legal_representative_qualification_code
                    WHERE p.cnpj_root=%s
                    ORDER BY p.partner_name,p.qualification_code
                """, (core["cnpj_root"],)).fetchall()
                reference_codes = {
                    "cnae": [core.get("primary_cnae"), *(core.get("secondary_cnaes") or [])],
                    "legal_nature": [company.get("legal_nature_code") if company else None],
                    "qualification": [company.get("responsible_qualification_code") if company else None],
                    "status_reason": [establishment.get("registration_status_reason_code") if establishment else None],
                    "country": [establishment.get("country_code") if establishment else None],
                }
                for kind, codes in reference_codes.items():
                    clean_codes = [code for code in codes if code]
                    if not clean_codes:
                        continue
                    rows = connection.execute("""
                        SELECT kind,code,label FROM rfb_current_aux_reference
                        WHERE kind=%s AND code=ANY(%s)
                    """, (kind, clean_codes)).fetchall()
                    references.update({f"{row['kind']}:{row['code']}": row["label"] for row in rows})
        serialize = lambda value: (
            {key: self._serializable(item) for key, item in dict(value).items()}
            if value else None
        )
        return {
            "core": self._company_detail_core(core),
            "company": serialize(company),
            "establishment": serialize(establishment),
            "simples": serialize(simples),
            "partners": [serialize(partner) for partner in partners],
            "references": references,
            "capabilities": capabilities.as_dict(),
        }

    @staticmethod
    def _matcher_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "row_number": item["local_id"],
            "local_id": item["local_id"],
            "company_name": item["name"],
            "street": item.get("address"),
            "address": item.get("address"),
            "city": item.get("municipality"),
            "state": item.get("uf"),
            "postal_code": item.get("postal_code"),
            "website": item.get("website"),
            "cnpj": item.get("cnpj"),
            "site_cnpjs": item.get("site_cnpjs", []),
            "site_names": item.get("site_names", []),
        }

    def _exact_candidates(self, items: list[dict[str, Any]], active_only: bool) -> dict[str, list[dict[str, Any]]]:
        requested = {}
        for item in items:
            values = set(item.get("site_cnpjs", []))
            if item.get("cnpj"):
                values.add(digits(item["cnpj"]))
            for cnpj in values:
                requested.setdefault(cnpj, []).append(item["local_id"])
        if not requested:
            return {}
        where_active = "AND is_active" if active_only else ""
        with self.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM rfb_establishments WHERE cnpj=ANY(%s) {where_active}",
                (list(requested),),
            ).fetchall()
        by_id: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            for local_id in requested.get(row["cnpj"], []):
                by_id.setdefault(local_id, []).append(self._candidate(row))
        return by_id

    def _location_candidates_for_item(
        self, item: dict[str, Any], active_only: bool
    ) -> list[dict[str, Any]]:
        postal_code = digits(item.get("postal_code"))
        city = normalize(item.get("municipality"))
        name = normalize(item.get("name"))
        if len(postal_code) != 8 or not city or not item.get("uf"):
            return []

        # One small indexed lookup per input is faster and more predictable than
        # joining a large UNNEST batch to all 72 million establishments.  Name is
        # used only to rank rows already narrowed to the exact location.
        active_sql = "AND is_active" if active_only else ""
        sql = f"""
            SELECT *
            FROM rfb_establishments
            WHERE uf=%s AND municipality=%s AND postal_code=%s {active_sql}
            ORDER BY greatest(
              similarity(normalized_legal_name,%s),
              similarity(normalized_trade_name,%s)
            ) DESC,cnpj
            LIMIT 60
        """
        try:
            with self.pool.connection() as connection:
                connection.execute(f"SET statement_timeout='{self.statement_timeout_ms}ms'")
                rows = connection.execute(sql, (item["uf"], city, postal_code, name, name)).fetchall()
            return [self._candidate(row) for row in rows]
        except Exception:
            return []

    def _location_candidates(self, items: list[dict[str, Any]], active_only: bool) -> dict[str, list[dict[str, Any]]]:
        eligible = [
            item for item in items
            if len(digits(item.get("postal_code"))) == 8
            and normalize(item.get("municipality"))
            and item.get("uf")
        ]
        if not eligible:
            return {}

        result: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=self.database_workers) as executor:
            futures = {
                executor.submit(self._location_candidates_for_item, item, active_only): item
                for item in eligible
            }
            for future in as_completed(futures):
                item = futures[future]
                candidates = future.result()
                if candidates:
                    result[item["local_id"]] = candidates
        return result

    def _fuzzy_candidates(self, item: dict[str, Any], active_only: bool) -> list[dict[str, Any]]:
        name = normalize(item["name"])
        if len(name) < 3:
            return []
        predicates = ["uf=%s"]
        where_values: list[Any] = [item["uf"]]
        city = normalize(item.get("municipality"))
        if city:
            predicates.append("municipality=%s")
            where_values.append(city)
        if active_only:
            predicates.append("is_active")
        predicates.append("(normalized_legal_name %% %s OR normalized_trade_name %% %s)")
        where_values.extend([name, name])
        sql = f"""
            SELECT *,greatest(similarity(normalized_legal_name,%s),similarity(normalized_trade_name,%s)) AS query_similarity
            FROM rfb_establishments
            WHERE {' AND '.join(predicates)}
            ORDER BY query_similarity DESC,cnpj
            LIMIT %s
        """
        def execute(threshold: float, timeout_ms: int) -> list[dict[str, Any]]:
            with self.pool.connection() as connection:
                connection.execute(f"SET statement_timeout='{timeout_ms}ms'")
                connection.execute(f"SET pg_trgm.similarity_threshold={threshold}")
                rows = connection.execute(sql, [name, name, *where_values, 30]).fetchall()
            return [self._candidate(row) for row in rows]

        try:
            return execute(0.46, self.statement_timeout_ms)
        except QueryCanceled:
            # A more selective retry avoids turning temporary database pressure
            # into a false "not found" result.
            try:
                return execute(0.55, max(5000, self.statement_timeout_ms * 2))
            except QueryCanceled:
                try:
                    return execute(0.68, max(8000, self.statement_timeout_ms * 3))
                except Exception:
                    return []
            except Exception:
                return []
        except Exception:
            return []

    @staticmethod
    def _merge_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged = {}
        for group in groups:
            for candidate in group:
                merged[candidate["cnpj"]] = candidate
        return list(merged.values())

    def match_batch(self, items: list[dict[str, Any]], active_only: bool = True) -> tuple[list[dict[str, Any]], str | None, int]:
        started = monotonic()
        version = self.current_version()
        exact = self._exact_candidates(items, active_only)
        location = self._location_candidates(items, active_only)

        candidates_by_id = {
            item["local_id"]: self._merge_candidates(exact.get(item["local_id"], []), location.get(item["local_id"], []))
            for item in items
        }
        preliminary = {
            item["local_id"]: decide(self._matcher_item(item), candidates_by_id[item["local_id"]])
            for item in items
        }
        fuzzy_items = [item for item in items if preliminary[item["local_id"]]["status"] != "confirmado"]
        if fuzzy_items:
            with ThreadPoolExecutor(max_workers=self.database_workers) as executor:
                futures = {executor.submit(self._fuzzy_candidates, item, active_only): item for item in fuzzy_items}
                for future in as_completed(futures):
                    item = futures[future]
                    candidates_by_id[item["local_id"]] = self._merge_candidates(
                        candidates_by_id[item["local_id"]], future.result()
                    )

        results = []
        for item in items:
            result = decide(self._matcher_item(item), candidates_by_id[item["local_id"]])
            result["local_id"] = item["local_id"]
            result.pop("row_number", None)
            result["dataset_version"] = version
            results.append(result)
        duration_ms = round((monotonic() - started) * 1000)
        return results, version, duration_ms
