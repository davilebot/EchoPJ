#!/usr/bin/env python3
"""Build a new unified Receita dataset directly from the official ZIP files.

The live tables are never written during the import.  Files are streamed into
versioned shadow relations and the existing ``UnifiedSearchBuilder`` performs
the short atomic publication only after exact validation.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from plataforma_receita.rfb_importer import read_semicolon_zip, sha256_file
from plataforma_receita.rfb_layout import (
    LAYOUTS,
    partner_row,
    simples_row,
    unified_establishment_row,
    unified_company_row,
)
from plataforma_receita.rfb_manifest import Manifest, ManifestFile, load_manifest
try:
    from scripts.build_unified_search import MAIN_COLUMNS, STATES, UnifiedSearchBuilder
except ModuleNotFoundError:  # Execucao direta: python scripts/import_unified_dataset.py
    from build_unified_search import MAIN_COLUMNS, STATES, UnifiedSearchBuilder


LOGGER = logging.getLogger("unified-dataset-import")
DOWNLOAD_ATTEMPTS = 6
REQUIRED_REFERENCE_KINDS = {
    "cnae", "country", "legal_nature", "municipality", "qualification", "status_reason",
}

COMPANY_COLUMNS = (
    "dataset_version", "cnpj_root", "legal_name", "legal_nature_code",
    "responsible_qualification_code", "company_size_code", "company_size",
    "share_capital", "federative_entity",
)
SIMPLES_COLUMNS = (
    "dataset_version", "cnpj_root", "is_simples", "simples_started_at",
    "simples_ended_at", "is_mei", "mei_started_at", "mei_ended_at",
)
REFERENCE_COLUMNS = ("dataset_version", "kind", "code", "label")
PARTNER_COLUMNS = (
    "dataset_version", "row_hash", "cnpj_root", "partner_type_code", "partner_type",
    "partner_name", "partner_document", "qualification_code", "qualification", "joined_at",
    "country_code", "country", "legal_representative_document", "legal_representative_name",
    "legal_representative_qualification_code", "legal_representative_qualification",
    "age_range_code", "age_range",
)
ESTABLISHMENT_BATCH_COLUMNS = (
    "dataset_version", "cnpj", "cnpj_root", "branch_order", "check_digits",
    "branch_type_code", "trade_name", "registration_status_code",
    "registration_status_date", "registration_status_reason_code", "foreign_city_name",
    "country_code", "opened_at", "primary_cnae", "secondary_cnaes", "street_type",
    "street", "street_number", "address_extra", "district", "postal_code", "uf",
    "municipality_code", "phone1_area_code", "phone1", "phone2_area_code", "phone2",
    "fax_area_code", "fax", "email", "special_status", "special_status_date",
)


@dataclass(frozen=True)
class ImportSettings:
    cache_directory: Path
    batch_rows: int = 100_000
    batch_pause_seconds: float = 0.05
    minimum_free_gb: float = 12.0
    maximum_database_gb: float = 175.0


def official_download_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": "EchoPJs-Unified-RFB/1.0"}
    match = re.search(r"/public\.php/dav/files/([^/]+)/", url)
    if match:
        token = match.group(1)
        credentials = base64.b64encode(f"{token}:".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"
    return headers


def branch_row(row: list[str], version: str) -> tuple | None:
    if len(row) < 30 or (row[3] or "").strip() != "2":
        return None
    parsed = unified_establishment_row(row, version)
    if parsed is None:
        return None
    return version, parsed[2], parsed[7] == "02"


class UnifiedDatasetImporter:
    def __init__(
        self,
        connection: psycopg.Connection[Any],
        manifest: Manifest,
        settings: ImportSettings,
    ):
        self.connection = connection
        self.manifest = manifest
        self.version = manifest.version
        self.settings = settings
        self.builder = UnifiedSearchBuilder(connection, self.version)
        self.suffix = self.builder.suffix
        self.company_stage = f"rfb_unified_company_build_{self.suffix}"
        self.simples_stage = f"rfb_unified_simples_build_{self.suffix}"
        self.reference_stage = f"rfb_unified_reference_build_{self.suffix}"
        self.branch_rows_stage = f"rfb_unified_branch_rows_build_{self.suffix}"
        self.branch_summary_stage = f"rfb_unified_branch_summary_build_{self.suffix}"
        self.establishment_batch = "rfb_unified_establishment_batch"
        self.settings.cache_directory.mkdir(parents=True, exist_ok=True)

    def initialize(self, *, restart: bool = False) -> None:
        if restart:
            self.builder.discard_incomplete()
        self.builder.initialize(restart=False)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS rfb_unified_imports (
              version text PRIMARY KEY,
              source_url text NOT NULL,
              status text NOT NULL,
              started_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              completed_at timestamptz,
              error text
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS rfb_unified_import_files (
              version text NOT NULL,
              phase text NOT NULL,
              file_name text NOT NULL,
              kind text NOT NULL,
              source_url text NOT NULL,
              expected_bytes bigint,
              status text NOT NULL DEFAULT 'pending',
              source_rows_processed bigint NOT NULL DEFAULT 0,
              rows_loaded bigint NOT NULL DEFAULT 0,
              bytes_downloaded bigint NOT NULL DEFAULT 0,
              started_at timestamptz,
              completed_at timestamptz,
              error text,
              PRIMARY KEY(version,phase,file_name)
            )
        """)
        self.connection.execute(f"""
            CREATE UNLOGGED TABLE IF NOT EXISTS {self.company_stage} (
              dataset_version text NOT NULL,cnpj_root text NOT NULL,
              legal_name text,legal_nature_code text,responsible_qualification_code text,
              company_size_code text,company_size text,share_capital numeric(20,2),
              federative_entity text
            )
        """)
        self.connection.execute(f"""
            CREATE UNLOGGED TABLE IF NOT EXISTS {self.simples_stage} (
              dataset_version text NOT NULL,cnpj_root text NOT NULL,is_simples boolean,
              simples_started_at date,simples_ended_at date,is_mei boolean,
              mei_started_at date,mei_ended_at date
            )
        """)
        self.connection.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.reference_stage} (
              dataset_version text NOT NULL,kind text NOT NULL,code text NOT NULL,label text NOT NULL,
              PRIMARY KEY(dataset_version,kind,code)
            )
        """)
        self.connection.execute(f"""
            CREATE UNLOGGED TABLE IF NOT EXISTS {self.branch_rows_stage} (
              dataset_version text NOT NULL,cnpj_root text NOT NULL,is_active boolean NOT NULL
            )
        """)
        self.connection.execute(f"""
            CREATE UNLOGGED TABLE IF NOT EXISTS {self.branch_summary_stage} (
              dataset_version text NOT NULL,cnpj_root text NOT NULL,
              branch_count integer NOT NULL,active_branch_count integer NOT NULL,
              PRIMARY KEY(dataset_version,cnpj_root)
            )
        """)
        self.connection.execute("""
            INSERT INTO rfb_unified_imports(version,source_url,status,error)
            VALUES (%s,%s,'building',NULL)
            ON CONFLICT(version) DO UPDATE SET source_url=excluded.source_url,
              status=CASE WHEN rfb_unified_imports.status='completed' THEN 'completed' ELSE 'building' END,
              updated_at=now(),error=NULL
        """, (self.version, self.manifest.source_url))
        self.connection.execute("""
            INSERT INTO dataset_versions(version,published_at,status,is_current,metadata)
            VALUES (%s,current_date,'importing',false,jsonb_build_object('source_url',%s::text))
            ON CONFLICT(version) DO UPDATE SET
              status=CASE WHEN dataset_versions.is_current THEN dataset_versions.status ELSE 'importing' END,
              metadata=dataset_versions.metadata || jsonb_build_object('source_url',%s::text),
              errors='[]'::jsonb
        """, (self.version, self.manifest.source_url, self.manifest.source_url))
        self.connection.execute(
            f"ALTER TABLE {self.company_stage} ADD COLUMN IF NOT EXISTS legal_name text"
        )
        self.connection.commit()

    def _ensure_capacity(self) -> None:
        free_gb = shutil.disk_usage(self.settings.cache_directory).free / 1024**3
        if free_gb < self.settings.minimum_free_gb:
            raise RuntimeError(
                f"espaco livre {free_gb:.1f} GB abaixo da margem de "
                f"{self.settings.minimum_free_gb:.1f} GB"
            )
        database_gb = self.connection.execute(
            "SELECT pg_database_size(current_database()) AS bytes"
        ).fetchone()["bytes"] / 1024**3
        if database_gb > self.settings.maximum_database_gb:
            raise RuntimeError(
                f"PostgreSQL em {database_gb:.1f} GB acima do limite de "
                f"{self.settings.maximum_database_gb:.1f} GB"
            )

    def _register_file(self, phase: str, entry: ManifestFile) -> dict[str, Any]:
        self.connection.execute("""
            INSERT INTO rfb_unified_import_files(
              version,phase,file_name,kind,source_url,expected_bytes,status
            ) VALUES (%s,%s,%s,%s,%s,%s,'pending')
            ON CONFLICT(version,phase,file_name) DO UPDATE SET
              kind=excluded.kind,source_url=excluded.source_url,
              expected_bytes=excluded.expected_bytes
        """, (self.version, phase, entry.name, entry.kind, entry.url, entry.size))
        self.connection.commit()
        return self.connection.execute("""
            SELECT * FROM rfb_unified_import_files
            WHERE version=%s AND phase=%s AND file_name=%s
        """, (self.version, phase, entry.name)).fetchone()

    def _download(self, entry: ManifestFile) -> Path:
        target = self.settings.cache_directory / entry.name
        if target.exists() and (entry.size is None or target.stat().st_size == entry.size):
            if entry.sha256 and sha256_file(target).lower() != entry.sha256.lower():
                target.unlink()
            else:
                return target
        request = urllib.request.Request(entry.url, headers=official_download_headers(entry.url))
        last_error: Exception | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            target.unlink(missing_ok=True)
            downloaded = 0
            try:
                LOGGER.info("baixando %s", entry.name)
                with urllib.request.urlopen(request, timeout=180) as response, target.open("wb") as output:
                    while chunk := response.read(4 * 1024 * 1024):
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded % (256 * 1024 * 1024) < 4 * 1024 * 1024:
                            self._ensure_capacity()
                if entry.size is not None and downloaded != entry.size:
                    raise ValueError(f"tamanho inesperado em {entry.name}: {downloaded}")
                if entry.sha256 and sha256_file(target).lower() != entry.sha256.lower():
                    raise ValueError(f"checksum invalido em {entry.name}")
                return target
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as error:
                last_error = error
                target.unlink(missing_ok=True)
                if attempt < DOWNLOAD_ATTEMPTS:
                    time.sleep(min(60, 2**attempt))
        raise last_error or RuntimeError(f"download sem resultado: {entry.name}")

    def _copy_rows(self, table: str, columns: Iterable[str], rows: list[tuple]) -> None:
        if not rows:
            return
        identifiers = sql.SQL(",").join(sql.Identifier(column) for column in columns)
        statement = sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(table), identifiers)
        with self.connection.cursor().copy(statement) as copy:
            for row in rows:
                copy.write_row(row)

    def _load_file(
        self,
        phase: str,
        entry: ManifestFile,
        parser: Callable[[list[str], str], tuple | None],
        flush: Callable[[list[tuple]], None],
        *,
        keep_cached: bool = False,
    ) -> None:
        progress = self._register_file(phase, entry)
        if progress["status"] == "completed":
            LOGGER.info("%s/%s ja concluido", phase, entry.name)
            return
        path = self._download(entry)
        self.connection.execute("""
            UPDATE rfb_unified_import_files
            SET status='running',started_at=coalesce(started_at,now()),error=NULL,
                bytes_downloaded=%s
            WHERE version=%s AND phase=%s AND file_name=%s
        """, (path.stat().st_size, self.version, phase, entry.name))
        self.connection.commit()
        source_progress = int(progress["source_rows_processed"])
        batch: list[tuple] = []
        last_flushed = source_progress
        source_index = 0
        try:
            for source_index, raw in enumerate(read_semicolon_zip(path), start=1):
                if source_index <= source_progress:
                    continue
                parsed = parser(raw, self.version)
                if parsed is not None:
                    batch.append(parsed)
                if (
                    len(batch) >= self.settings.batch_rows
                    or source_index - last_flushed >= self.settings.batch_rows
                ):
                    self._ensure_capacity()
                    flush(batch)
                    self.connection.execute("""
                        UPDATE rfb_unified_import_files
                        SET source_rows_processed=%s,rows_loaded=rows_loaded+%s
                        WHERE version=%s AND phase=%s AND file_name=%s
                    """, (source_index, len(batch), self.version, phase, entry.name))
                    self.connection.commit()
                    batch = []
                    last_flushed = source_index
                    if self.settings.batch_pause_seconds:
                        time.sleep(self.settings.batch_pause_seconds)
            if source_index > last_flushed or batch:
                self._ensure_capacity()
                flush(batch)
                self.connection.execute("""
                    UPDATE rfb_unified_import_files
                    SET source_rows_processed=%s,rows_loaded=rows_loaded+%s
                    WHERE version=%s AND phase=%s AND file_name=%s
                """, (source_index, len(batch), self.version, phase, entry.name))
            self.connection.execute("""
                UPDATE rfb_unified_import_files
                SET status='completed',completed_at=now(),error=NULL
                WHERE version=%s AND phase=%s AND file_name=%s
            """, (self.version, phase, entry.name))
            self.connection.commit()
            if not keep_cached:
                path.unlink(missing_ok=True)
        except Exception as error:
            self.connection.rollback()
            self.connection.execute("""
                UPDATE rfb_unified_import_files SET status='failed',error=%s
                WHERE version=%s AND phase=%s AND file_name=%s
            """, (type(error).__name__, self.version, phase, entry.name))
            self.connection.commit()
            raise

    def _files(self, *kinds: str) -> list[ManifestFile]:
        return sorted(
            (entry for entry in self.manifest.files if entry.kind in kinds),
            key=lambda entry: (entry.kind, entry.name),
        )

    def load_references(self) -> None:
        for entry in self._files(*(kind for kind in LAYOUTS if kind.startswith("reference_"))):
            layout = LAYOUTS[entry.kind]
            self._load_file(
                "references", entry, layout.parser,
                lambda rows: self._insert_references(rows),
            )

    def _insert_references(self, rows: list[tuple]) -> None:
        if not rows:
            return
        temporary = "rfb_unified_reference_batch"
        self.connection.execute(f"""
            CREATE TEMP TABLE IF NOT EXISTS {temporary}
            (LIKE {self.reference_stage} INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS
        """)
        self.connection.execute(f"TRUNCATE {temporary}")
        self._copy_rows(temporary, REFERENCE_COLUMNS, rows)
        self.connection.execute(f"""
            INSERT INTO {self.reference_stage}({','.join(REFERENCE_COLUMNS)})
            SELECT {','.join(REFERENCE_COLUMNS)} FROM {temporary}
            ON CONFLICT(dataset_version,kind,code) DO UPDATE SET label=excluded.label
        """)

    def load_companies(self) -> None:
        self._reset_legacy_company_stage_without_names()
        for entry in self._files("companies"):
            self._load_file(
                "companies", entry, unified_company_row,
                lambda rows: self._copy_rows(self.company_stage, COMPANY_COLUMNS, rows),
            )
        self._create_company_index()
        self.connection.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(self.company_stage)))
        self.connection.commit()

    def _reset_legacy_company_stage_without_names(self) -> None:
        counts = self.connection.execute(f"""
            SELECT count(*) AS rows,count(legal_name) AS named_rows
            FROM {self.company_stage} WHERE dataset_version=%s
        """, (self.version,)).fetchone()
        if not counts["rows"] or counts["named_rows"]:
            return
        LOGGER.warning(
            "staging de Empresas sem razão social; recarregando somente esta fase"
        )
        self.connection.execute(
            sql.SQL("DROP INDEX IF EXISTS {}").format(
                sql.Identifier(f"{self.company_stage}_root_idx")
            )
        )
        self.connection.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(self.company_stage)))
        self.connection.execute("""
            UPDATE rfb_unified_import_files
            SET status='pending',source_rows_processed=0,rows_loaded=0,
                bytes_downloaded=0,started_at=NULL,completed_at=NULL,error=NULL
            WHERE version=%s AND phase='companies'
        """, (self.version,))
        self.connection.commit()

    def _create_company_index(self) -> None:
        statement = (
            f"CREATE UNIQUE INDEX IF NOT EXISTS {self.company_stage}_root_idx "
            f"ON {self.company_stage}(dataset_version,cnpj_root)"
        )
        try:
            self.connection.execute(statement)
            self.connection.commit()
        except psycopg.errors.UniqueViolation:
            self.connection.rollback()
            LOGGER.warning("raízes repetidas na fonte de Empresas; consolidando a linha mais completa")
            self._deduplicate_companies()
            self.connection.execute(statement)
            self.connection.commit()

    def _deduplicate_companies(self) -> None:
        duplicate_keys = "rfb_unified_company_duplicate_keys"
        self.connection.execute(f"DROP TABLE IF EXISTS {duplicate_keys}")
        self.connection.execute(f"""
            CREATE TEMP TABLE {duplicate_keys} ON COMMIT DROP AS
            SELECT dataset_version,cnpj_root
            FROM {self.company_stage}
            GROUP BY dataset_version,cnpj_root HAVING count(*)>1
        """)
        duplicate_count = self.connection.execute(
            f"SELECT count(*) AS rows FROM {duplicate_keys}"
        ).fetchone()["rows"]
        if not duplicate_count:
            raise RuntimeError("indice unico falhou, mas nenhuma raiz duplicada foi localizada")
        result = self.connection.execute(f"""
            DELETE FROM {self.company_stage} target
            USING (
              SELECT row_ctid FROM (
                SELECT company.ctid AS row_ctid,
                       row_number() OVER (
                         PARTITION BY company.dataset_version,company.cnpj_root
                         ORDER BY
                           ((company.legal_nature_code IS NOT NULL AND company.legal_nature_code<>'0000')::int * 8
                            +(company.responsible_qualification_code IS NOT NULL
                              AND company.responsible_qualification_code<>'00')::int * 4
                            +(company.company_size_code IS NOT NULL
                              AND company.company_size_code<>'00')::int * 2
                            +(company.federative_entity IS NOT NULL)::int
                            +(coalesce(company.share_capital,0)<>0)::int) DESC,
                           company.legal_nature_code DESC NULLS LAST,
                           company.responsible_qualification_code DESC NULLS LAST,
                           company.company_size_code DESC NULLS LAST,
                           md5(ROW(company.legal_nature_code,
                                   company.responsible_qualification_code,
                                   company.company_size_code,company.company_size,
                                   company.share_capital,company.federative_entity)::text)
                       ) AS position
                FROM {self.company_stage} company
                JOIN {duplicate_keys} duplicate
                  USING(dataset_version,cnpj_root)
              ) ranked WHERE position>1
            ) discarded
            WHERE target.ctid=discarded.row_ctid
        """)
        LOGGER.warning(
            "%s raiz(es) repetida(s); %s linha(s) menos completa(s) removida(s)",
            duplicate_count,
            result.rowcount,
        )
        self.connection.commit()

    def load_simples(self) -> None:
        for entry in self._files("simples"):
            self._load_file(
                "simples", entry, simples_row,
                lambda rows: self._copy_rows(self.simples_stage, SIMPLES_COLUMNS, rows),
            )
        self.connection.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {self.simples_stage}_root_idx "
            f"ON {self.simples_stage}(dataset_version,cnpj_root)"
        )
        self.connection.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(self.simples_stage)))
        self.connection.commit()

    def _reference_labels(self) -> dict[str, dict[str, str]]:
        rows = self.connection.execute(f"""
            SELECT kind,code,label FROM {self.reference_stage} WHERE dataset_version=%s
        """, (self.version,)).fetchall()
        labels: dict[str, dict[str, str]] = {}
        for row in rows:
            labels.setdefault(row["kind"], {})[row["code"]] = row["label"]
        return labels

    def load_partners(self) -> None:
        labels = self._reference_labels()
        qualifications = labels.get("qualification", {})
        countries = labels.get("country", {})

        def parse(raw: list[str], version: str) -> tuple | None:
            row = partner_row(raw, version)
            if row is None:
                return None
            return (
                row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                qualifications.get(row[7]), row[8], row[9], countries.get(row[9]),
                row[10], row[11], row[12], qualifications.get(row[12]), row[13], row[14],
            )

        for entry in self._files("partners"):
            self._load_file(
                "partners", entry, parse,
                lambda rows: self._insert_partners(rows),
            )
        self.connection.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(self.builder.partner_summary)))
        self.connection.execute(f"""
            INSERT INTO {self.builder.partner_summary}(
              dataset_version,cnpj_root,partner_count,partner_age_codes
            )
            SELECT dataset_version,cnpj_root,count(*)::integer,
                   coalesce(array_agg(DISTINCT age_range_code ORDER BY age_range_code)
                     FILTER (WHERE age_range_code IS NOT NULL),ARRAY[]::text[])
            FROM {self.builder.partners_next}
            WHERE dataset_version=%s GROUP BY dataset_version,cnpj_root
        """, (self.version,))
        self.connection.execute(
            "UPDATE rfb_unified_builds SET partners_ready=true,updated_at=now() WHERE version=%s",
            (self.version,),
        )
        self.connection.commit()

    def _insert_partners(self, rows: list[tuple]) -> None:
        if not rows:
            return
        temporary = "rfb_unified_partner_batch"
        self.connection.execute(f"""
            CREATE TEMP TABLE IF NOT EXISTS {temporary}
            (LIKE {self.builder.partners_next} INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS
        """)
        self.connection.execute(f"TRUNCATE {temporary}")
        self._copy_rows(temporary, PARTNER_COLUMNS, rows)
        self.connection.execute(f"""
            INSERT INTO {self.builder.partners_next}({','.join(PARTNER_COLUMNS)})
            SELECT {','.join(PARTNER_COLUMNS)} FROM {temporary}
            ON CONFLICT(dataset_version,row_hash) DO NOTHING
        """)

    def build_branch_summary(self) -> None:
        for entry in self._files("establishments"):
            self._load_file(
                "branch_counts", entry, branch_row,
                lambda rows: self._copy_rows(
                    self.branch_rows_stage,
                    ("dataset_version", "cnpj_root", "is_active"),
                    rows,
                ),
                keep_cached=True,
            )
        self.connection.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(self.branch_summary_stage)))
        self.connection.execute(f"""
            INSERT INTO {self.branch_summary_stage}(
              dataset_version,cnpj_root,branch_count,active_branch_count
            )
            SELECT dataset_version,cnpj_root,count(*)::integer,
                   count(*) FILTER (WHERE is_active)::integer
            FROM {self.branch_rows_stage}
            WHERE dataset_version=%s GROUP BY dataset_version,cnpj_root
        """, (self.version,))
        self.connection.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(self.branch_summary_stage)))
        self.connection.commit()

    def load_establishments(self) -> None:
        self._prepare_establishment_batch()
        for entry in self._files("establishments"):
            self._load_file(
                "establishments", entry, unified_establishment_row,
                self._insert_establishments,
            )
        states = self.connection.execute(f"""
            SELECT array_agg(DISTINCT uf ORDER BY uf) AS states
            FROM {self.builder.main_next} WHERE dataset_version=%s
        """, (self.version,)).fetchone()["states"]
        self.connection.execute("""
            UPDATE rfb_unified_builds SET states_completed=%s,updated_at=now()
            WHERE version=%s
        """, (states or [], self.version))
        self.connection.commit()

    def _prepare_establishment_batch(self) -> None:
        self.connection.execute(f"""
            CREATE TEMP TABLE IF NOT EXISTS {self.establishment_batch} (
              dataset_version text,cnpj text,cnpj_root text,branch_order text,check_digits text,
              branch_type_code text,trade_name text,registration_status_code text,
              registration_status_date date,registration_status_reason_code text,
              foreign_city_name text,country_code text,opened_at date,primary_cnae text,
              secondary_cnaes text[],street_type text,street text,street_number text,
              address_extra text,district text,postal_code text,uf text,municipality_code text,
              phone1_area_code text,phone1 text,phone2_area_code text,phone2 text,
              fax_area_code text,fax text,email text,special_status text,special_status_date date
            ) ON COMMIT PRESERVE ROWS
        """)
        self.connection.commit()

    def _insert_establishments(self, rows: list[tuple]) -> None:
        if not rows:
            return
        self.connection.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(self.establishment_batch)))
        self._copy_rows(self.establishment_batch, ESTABLISHMENT_BATCH_COLUMNS, rows)
        self.connection.execute("SET LOCAL work_mem='128MB'")
        self.connection.execute(f"""
            INSERT INTO {self.builder.main_next} ({MAIN_COLUMNS})
            SELECT
              x.cnpj,x.cnpj_root,x.branch_order,x.check_digits,c.legal_name,x.trade_name,
              immutable_unaccent(upper(coalesce(c.legal_name,''))),
              immutable_unaccent(upper(coalesce(x.trade_name,''))),
              CASE x.registration_status_code
                WHEN '01' THEN 'NULA' WHEN '02' THEN 'ATIVA' WHEN '03' THEN 'SUSPENSA'
                WHEN '04' THEN 'INAPTA' WHEN '08' THEN 'BAIXADA' ELSE 'NAO INFORMADA' END,
              x.registration_status_code,x.registration_status_date,
              x.registration_status_reason_code,status_reason.label,x.opened_at,
              coalesce(c.company_size_code,'00'),coalesce(c.company_size,'NAO INFORMADO'),
              c.share_capital,c.legal_nature_code,nature.label,
              c.responsible_qualification_code,responsible.label,c.federative_entity,
              x.primary_cnae,cnae.label,x.secondary_cnaes,x.street_type,x.street,
              x.street_number,x.address_extra,x.district,x.postal_code,x.municipality_code,
              municipality.label,x.uf,x.branch_type_code,x.foreign_city_name,x.country_code,
              country.label,x.phone1_area_code,x.phone1,x.phone2_area_code,x.phone2,
              x.fax_area_code,x.fax,x.email,x.special_status,x.special_status_date,
              s.is_simples,s.simples_started_at,s.simples_ended_at,s.is_mei,
              s.mei_started_at,s.mei_ended_at,coalesce(b.branch_count,0),
              coalesce(b.active_branch_count,0),coalesce(p.partner_count,0),
              coalesce(p.partner_age_codes,ARRAY[]::text[]),
              x.registration_status_code='02',x.dataset_version,now()
            FROM {self.establishment_batch} x
            LEFT JOIN {self.company_stage} c
              ON c.dataset_version=x.dataset_version AND c.cnpj_root=x.cnpj_root
            LEFT JOIN {self.simples_stage} s
              ON s.dataset_version=x.dataset_version AND s.cnpj_root=x.cnpj_root
            LEFT JOIN {self.branch_summary_stage} b
              ON b.dataset_version=x.dataset_version AND b.cnpj_root=x.cnpj_root
            LEFT JOIN {self.builder.partner_summary} p
              ON p.dataset_version=x.dataset_version AND p.cnpj_root=x.cnpj_root
            LEFT JOIN {self.reference_stage} cnae
              ON cnae.dataset_version=x.dataset_version AND cnae.kind='cnae'
             AND cnae.code=x.primary_cnae
            LEFT JOIN {self.reference_stage} nature
              ON nature.dataset_version=x.dataset_version AND nature.kind='legal_nature'
             AND nature.code=c.legal_nature_code
            LEFT JOIN {self.reference_stage} responsible
              ON responsible.dataset_version=x.dataset_version AND responsible.kind='qualification'
             AND responsible.code=c.responsible_qualification_code
            LEFT JOIN {self.reference_stage} status_reason
              ON status_reason.dataset_version=x.dataset_version AND status_reason.kind='status_reason'
             AND status_reason.code=x.registration_status_reason_code
            LEFT JOIN {self.reference_stage} country
              ON country.dataset_version=x.dataset_version AND country.kind='country'
             AND country.code=x.country_code
            LEFT JOIN {self.reference_stage} municipality
              ON municipality.dataset_version=x.dataset_version AND municipality.kind='municipality'
             AND municipality.code=x.municipality_code
            ON CONFLICT(uf,cnpj) DO NOTHING
        """)

    def _phase_rows(self, phase: str) -> int:
        row = self.connection.execute("""
            SELECT coalesce(sum(rows_loaded),0) AS rows
            FROM rfb_unified_import_files
            WHERE version=%s AND phase=%s AND status='completed'
        """, (self.version, phase)).fetchone()
        return int(row["rows"])

    def validate(self) -> dict[str, Any]:
        source_establishments = self._phase_rows("establishments")
        source_partners = self._phase_rows("partners")
        target = self.connection.execute(f"""
            SELECT count(*) AS rows,
                   count(*) FILTER (WHERE is_active) AS active_rows,
                   count(*) FILTER (WHERE NOT is_active) AS inactive_rows,
                   count(*) FILTER (WHERE legal_name IS NULL) AS missing_legal_name,
                   count(*) FILTER (WHERE municipality IS NULL) AS missing_municipality,
                   array_agg(DISTINCT uf ORDER BY uf) AS states
            FROM {self.builder.main_next} WHERE dataset_version=%s
        """, (self.version,)).fetchone()
        target_partners = self.connection.execute(f"""
            SELECT count(*) AS rows FROM {self.builder.partners_next} WHERE dataset_version=%s
        """, (self.version,)).fetchone()["rows"]
        reference_kinds = self.connection.execute(f"""
            SELECT array_agg(DISTINCT kind ORDER BY kind) AS kinds
            FROM {self.reference_stage} WHERE dataset_version=%s
        """, (self.version,)).fetchone()["kinds"] or []
        states = target["states"] or []
        validation = {
            "source_establishments": source_establishments,
            "target_establishments": int(target["rows"]),
            "source_partners": source_partners,
            "target_partners": int(target_partners),
            "active_establishments": int(target["active_rows"]),
            "inactive_establishments": int(target["inactive_rows"]),
            "missing_legal_name": int(target["missing_legal_name"]),
            "missing_municipality": int(target["missing_municipality"]),
            "states": states,
            "reference_kinds": reference_kinds,
        }
        valid = (
            source_establishments == target["rows"]
            and source_partners == target_partners
            and set(states) == set(STATES)
            and REQUIRED_REFERENCE_KINDS.issubset(reference_kinds)
            and target["missing_legal_name"] == 0
        )
        self.connection.execute("""
            UPDATE rfb_unified_builds
            SET status=%s,validated=%s,validation=%s::jsonb,updated_at=now(),error=%s
            WHERE version=%s
        """, (
            "validated" if valid else "validation_failed",
            valid,
            json.dumps(validation, ensure_ascii=False),
            None if valid else "divergencia na validacao direta",
            self.version,
        ))
        self.connection.commit()
        if not valid:
            raise RuntimeError(f"validacao falhou: {validation}")
        return validation

    def drop_large_staging(self) -> None:
        for relation in (
            self.company_stage,
            self.simples_stage,
            self.branch_rows_stage,
            self.branch_summary_stage,
            self.builder.partner_summary,
        ):
            self.connection.execute(
                sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(relation))
            )
        self.connection.commit()

    def complete(self, *, publish: bool) -> dict[str, Any]:
        validation = self.validate()
        self.drop_large_staging()
        self.builder.create_indexes()
        validation = self.validate()
        if publish:
            self.builder.publish()
            self.connection.execute(
                sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(self.reference_stage))
            )
            self.connection.execute("""
                UPDATE rfb_unified_imports
                SET status='completed',completed_at=now(),updated_at=now(),error=NULL
                WHERE version=%s
            """, (self.version,))
            self.connection.commit()
        return validation

    def run(
        self,
        *,
        restart: bool = False,
        publish: bool = False,
        stop_after: str | None = None,
    ) -> dict[str, Any]:
        self.initialize(restart=restart)
        try:
            self.load_references()
            if stop_after == "references":
                return {"version": self.version, "stopped_after": stop_after}
            self.load_companies()
            if stop_after == "companies":
                return {"version": self.version, "stopped_after": stop_after}
            self.load_simples()
            if stop_after == "simples":
                return {"version": self.version, "stopped_after": stop_after}
            self.load_partners()
            if stop_after == "partners":
                return {"version": self.version, "stopped_after": stop_after}
            self.build_branch_summary()
            if stop_after == "branch_counts":
                return {"version": self.version, "stopped_after": stop_after}
            self.load_establishments()
            if stop_after == "establishments":
                return {"version": self.version, "stopped_after": stop_after}
            return self.complete(publish=publish)
        except Exception as error:
            self.connection.rollback()
            self.connection.execute("""
                UPDATE rfb_unified_imports
                SET status='failed',updated_at=now(),error=%s WHERE version=%s
            """, (type(error).__name__, self.version))
            self.connection.execute("""
                UPDATE dataset_versions SET status='failed',
                  errors=errors || jsonb_build_array(%s::text)
                WHERE version=%s AND NOT is_current
            """, (str(error), self.version))
            self.connection.commit()
            raise

    def publish_only(self) -> dict[str, Any]:
        validation = self.validate()
        self.builder.publish()
        self.connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(self.reference_stage))
        )
        self.connection.execute("""
            UPDATE rfb_unified_imports
            SET status='completed',completed_at=now(),updated_at=now(),error=NULL
            WHERE version=%s
        """, (self.version,))
        self.connection.commit()
        return validation


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa a competencia oficial diretamente na tabela unificada shadow"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/rfb-unified-cache"))
    parser.add_argument("--batch-rows", type=int, default=100_000)
    parser.add_argument("--batch-pause-seconds", type=float, default=0.05)
    parser.add_argument("--minimum-free-gb", type=float, default=12.0)
    parser.add_argument("--maximum-database-gb", type=float, default=175.0)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument(
        "--stop-after",
        choices=("references", "companies", "simples", "partners", "branch_counts", "establishments"),
    )
    args = parser.parse_args()
    dsn = os.getenv("ADMIN_POSTGRES_DSN") or os.getenv("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("configure ADMIN_POSTGRES_DSN sem gravar credenciais no repositorio")
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    manifest = load_manifest(args.manifest)
    settings = ImportSettings(
        cache_directory=args.cache_dir,
        batch_rows=args.batch_rows,
        batch_pause_seconds=args.batch_pause_seconds,
        minimum_free_gb=args.minimum_free_gb,
        maximum_database_gb=args.maximum_database_gb,
    )
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as connection:
        connection.execute("SET statement_timeout=0")
        importer = UnifiedDatasetImporter(connection, manifest, settings)
        importer.builder.lock()
        try:
            if args.publish_only:
                result = importer.publish_only()
            else:
                result = importer.run(
                    restart=args.restart,
                    publish=args.publish,
                    stop_after=args.stop_after,
                )
            print(json.dumps(result, ensure_ascii=False))
        finally:
            importer.builder.unlock()


if __name__ == "__main__":
    main()
