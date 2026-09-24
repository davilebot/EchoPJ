"""Build and publish the consolidated Receita search relation.

The builder is resumable by state. It never mutates the live relations until
``--publish`` and keeps the previous parents under versioned legacy names for
rollback. Run with an administrative PostgreSQL DSN; the application DSN is
intentionally read-only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


LOGGER = logging.getLogger("unified-search-builder")
ADVISORY_LOCK = "echopjs-unified-search-build"
STATES = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
MAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS {main_next} (
  cnpj text NOT NULL,
  cnpj_root text NOT NULL,
  branch_order text NOT NULL,
  check_digits text NOT NULL,
  legal_name text,
  trade_name text,
  normalized_legal_name text,
  normalized_trade_name text,
  registration_status text NOT NULL,
  registration_status_code text NOT NULL,
  registration_status_date date,
  registration_status_reason_code text,
  registration_status_reason text,
  opened_at date,
  company_size_code text,
  company_size text,
  share_capital numeric(20,2),
  legal_nature_code text,
  legal_nature text,
  responsible_qualification_code text,
  responsible_qualification text,
  federative_entity text,
  primary_cnae text,
  primary_cnae_description text,
  secondary_cnaes text[],
  street_type text,
  street text,
  street_number text,
  address_extra text,
  district text,
  postal_code text,
  municipality_code text,
  municipality text,
  uf text NOT NULL,
  branch_type_code text,
  foreign_city_name text,
  country_code text,
  country text,
  phone1_area_code text,
  phone1 text,
  phone2_area_code text,
  phone2 text,
  fax_area_code text,
  fax text,
  email text,
  special_status text,
  special_status_date date,
  is_simples boolean,
  simples_started_at date,
  simples_ended_at date,
  is_mei boolean,
  mei_started_at date,
  mei_ended_at date,
  branch_count integer NOT NULL DEFAULT 0,
  active_branch_count integer NOT NULL DEFAULT 0,
  partner_count integer NOT NULL DEFAULT 0,
  partner_age_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
  is_active boolean NOT NULL,
  dataset_version text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  has_email boolean GENERATED ALWAYS AS (nullif(email,'') IS NOT NULL) STORED,
  has_phone boolean GENERATED ALWAYS AS (nullif(phone1,'') IS NOT NULL OR nullif(phone2,'') IS NOT NULL) STORED,
  PRIMARY KEY (uf,cnpj),
  CHECK (cnpj ~ '^[0-9A-Z]{{14}}$'),
  CHECK (uf ~ '^[A-Z]{{2}}$')
) PARTITION BY LIST (uf)
"""

PARTNER_SCHEMA = """
CREATE TABLE IF NOT EXISTS {partners_next} (
  dataset_version text NOT NULL,
  row_hash bytea NOT NULL CHECK (octet_length(row_hash)=32),
  cnpj_root text NOT NULL CHECK (cnpj_root ~ '^[0-9A-Z]{{8}}$'),
  partner_type_code text,
  partner_type text,
  partner_name text,
  partner_document text,
  qualification_code text,
  qualification text,
  joined_at date,
  country_code text,
  country text,
  legal_representative_document text,
  legal_representative_name text,
  legal_representative_qualification_code text,
  legal_representative_qualification text,
  age_range_code text,
  age_range text,
  PRIMARY KEY (dataset_version,row_hash)
)
"""

MAIN_COLUMNS = """
  cnpj,cnpj_root,branch_order,check_digits,legal_name,trade_name,
  normalized_legal_name,normalized_trade_name,registration_status,
  registration_status_code,registration_status_date,
  registration_status_reason_code,registration_status_reason,opened_at,
  company_size_code,company_size,share_capital,legal_nature_code,legal_nature,
  responsible_qualification_code,responsible_qualification,federative_entity,
  primary_cnae,primary_cnae_description,secondary_cnaes,street_type,street,
  street_number,address_extra,district,postal_code,municipality_code,municipality,uf,
  branch_type_code,foreign_city_name,country_code,country,phone1_area_code,phone1,
  phone2_area_code,phone2,fax_area_code,fax,email,special_status,special_status_date,
  is_simples,simples_started_at,simples_ended_at,is_mei,mei_started_at,mei_ended_at,
  branch_count,active_branch_count,partner_count,partner_age_codes,is_active,
  dataset_version,updated_at
"""


@dataclass
class BuildState:
    version: str
    partners_ready: bool
    states_completed: set[str]
    indexes_ready: bool
    validated: bool
    published: bool


class UnifiedSearchBuilder:
    def __init__(self, connection: psycopg.Connection, version: str):
        self.connection = connection
        self.version = version
        self.suffix = re.sub(r"[^0-9a-z]+", "_", version.lower()).strip("_")
        if not self.suffix:
            raise ValueError("versao invalida")
        self.main_next = f"rfb_establishments_build_{self.suffix}"
        self.partners_next = f"rfb_partners_build_{self.suffix}"
        self.partner_summary = f"rfb_partner_search_summary_build_{self.suffix}"

    def _partition(self, state: str) -> str:
        return f"{self.main_next}_{state.lower()}"

    def _copy_select_grants(self, source: str, target: str) -> None:
        grantees = self.connection.execute("""
            SELECT DISTINCT grantee
            FROM information_schema.role_table_grants
            WHERE table_schema='public' AND table_name=%s AND privilege_type='SELECT'
              AND grantee<>current_user
        """, (source,)).fetchall()
        for row in grantees:
            self.connection.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    sql.Identifier(target), sql.Identifier(row["grantee"]),
                )
            )

    def _refresh_partner_view(self) -> None:
        self.connection.execute("""
            CREATE OR REPLACE VIEW rfb_current_partners AS
            SELECT p.dataset_version,p.row_hash,p.cnpj_root,p.partner_type_code,
                   p.partner_type,p.partner_name,p.partner_document,p.qualification_code,
                   p.joined_at,p.country_code,p.legal_representative_document,
                   p.legal_representative_name,p.legal_representative_qualification_code,
                   p.age_range_code,p.age_range,p.qualification,p.country,
                   p.legal_representative_qualification
            FROM rfb_partners p
            JOIN dataset_versions d ON d.version=p.dataset_version
            WHERE d.is_current AND d.status='ready'
        """)

    def lock(self) -> None:
        locked = self.connection.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (ADVISORY_LOCK,)
        ).fetchone()["locked"]
        if not locked:
            raise RuntimeError("ja existe uma construcao unificada em andamento")

    def unlock(self) -> None:
        # A session-level advisory lock must also be released after a statement
        # aborts the current transaction.
        self.connection.rollback()
        self.connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK,))
        self.connection.commit()

    def initialize(self, *, restart: bool = False) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS rfb_unified_builds (
              version text PRIMARY KEY,
              status text NOT NULL,
              partners_ready boolean NOT NULL DEFAULT false,
              states_completed text[] NOT NULL DEFAULT ARRAY[]::text[],
              indexes_ready boolean NOT NULL DEFAULT false,
              validated boolean NOT NULL DEFAULT false,
              published boolean NOT NULL DEFAULT false,
              validation jsonb NOT NULL DEFAULT '{}'::jsonb,
              started_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              error text
            )
        """)
        existing = self.connection.execute(
            "SELECT version,published FROM rfb_unified_builds ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if existing and existing["version"] != self.version and not existing["published"]:
            raise RuntimeError(f"a construcao {existing['version']} ainda nao foi publicada")
        if restart:
            self.connection.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(self.main_next)))
            self.connection.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(self.partners_next)))
            self.connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(self.partner_summary)))
            self.connection.execute("DELETE FROM rfb_unified_builds WHERE version=%s", (self.version,))
        self.connection.execute(
            """INSERT INTO rfb_unified_builds(version,status)
               VALUES (%s,'preparing') ON CONFLICT(version) DO NOTHING""",
            (self.version,),
        )
        self.connection.execute(MAIN_SCHEMA.format(main_next=self.main_next))
        for state in STATES:
            self.connection.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} PARTITION OF {} FOR VALUES IN ({})").format(
                    sql.Identifier(self._partition(state)),
                    sql.Identifier(self.main_next),
                    sql.Literal(state),
                )
            )
        self.connection.execute(PARTNER_SCHEMA.format(partners_next=self.partners_next))
        self.connection.execute(f"""
            CREATE UNLOGGED TABLE IF NOT EXISTS {self.partner_summary} (
              dataset_version text NOT NULL,
              cnpj_root text NOT NULL,
              partner_count integer NOT NULL,
              partner_age_codes text[] NOT NULL,
              PRIMARY KEY(dataset_version,cnpj_root)
            )
        """)
        self.connection.execute(
            "UPDATE rfb_unified_builds SET status='building',updated_at=now(),error=NULL WHERE version=%s",
            (self.version,),
        )
        self.connection.commit()

    def state(self) -> BuildState:
        row = self.connection.execute(
            "SELECT * FROM rfb_unified_builds WHERE version=%s", (self.version,)
        ).fetchone()
        if not row:
            raise RuntimeError("execute a preparacao antes da construcao")
        return BuildState(
            version=row["version"],
            partners_ready=bool(row["partners_ready"]),
            states_completed=set(row["states_completed"] or []),
            indexes_ready=bool(row["indexes_ready"]),
            validated=bool(row["validated"]),
            published=bool(row["published"]),
        )

    def build_partners(self) -> None:
        if self.state().partners_ready:
            LOGGER.info("socios e resumo ja preparados")
            return
        LOGGER.info("preparando socios com rotulos incorporados")
        self.connection.execute(
            sql.SQL("TRUNCATE {},{}").format(
                sql.Identifier(self.partners_next), sql.Identifier(self.partner_summary),
            )
        )
        self.connection.execute(f"""
            INSERT INTO {self.partners_next}(
              dataset_version,row_hash,cnpj_root,partner_type_code,partner_type,
              partner_name,partner_document,qualification_code,qualification,joined_at,
              country_code,country,legal_representative_document,legal_representative_name,
              legal_representative_qualification_code,legal_representative_qualification,
              age_range_code,age_range
            )
            SELECT p.dataset_version,p.row_hash,p.cnpj_root,p.partner_type_code,p.partner_type,
                   p.partner_name,p.partner_document,p.qualification_code,q.label,p.joined_at,
                   p.country_code,co.label,p.legal_representative_document,
                   p.legal_representative_name,p.legal_representative_qualification_code,
                   rq.label,p.age_range_code,p.age_range
            FROM rfb_partners p
            LEFT JOIN rfb_aux_reference q
              ON q.dataset_version=p.dataset_version AND q.kind='qualification'
             AND q.code=p.qualification_code
            LEFT JOIN rfb_aux_reference co
              ON co.dataset_version=p.dataset_version AND co.kind='country'
             AND co.code=p.country_code
            LEFT JOIN rfb_aux_reference rq
              ON rq.dataset_version=p.dataset_version AND rq.kind='qualification'
             AND rq.code=p.legal_representative_qualification_code
            WHERE p.dataset_version=%s
        """, (self.version,))
        self.connection.execute(f"""
            INSERT INTO {self.partner_summary}(dataset_version,cnpj_root,partner_count,partner_age_codes)
            SELECT dataset_version,cnpj_root,count(*)::integer,
                   coalesce(array_agg(DISTINCT age_range_code ORDER BY age_range_code)
                     FILTER (WHERE age_range_code IS NOT NULL),ARRAY[]::text[])
            FROM {self.partners_next}
            WHERE dataset_version=%s
            GROUP BY dataset_version,cnpj_root
        """, (self.version,))
        self.connection.execute(
            "UPDATE rfb_unified_builds SET partners_ready=true,updated_at=now() WHERE version=%s",
            (self.version,),
        )
        self.connection.commit()

    def _insert_establishments(self, state: str | None = None) -> None:
        state_predicate = " AND e.uf=%s" if state else ""
        parameters = (self.version, state) if state else (self.version,)
        self.connection.execute(f"""
            INSERT INTO {self.main_next} ({MAIN_COLUMNS})
            SELECT
              e.cnpj,e.cnpj_root,e.branch_order,e.check_digits,e.legal_name,e.trade_name,
              e.normalized_legal_name,e.normalized_trade_name,e.registration_status,
              e.registration_status_code,e.registration_status_date,
              x.registration_status_reason_code,status_reason.label,
              coalesce(e.opened_at,x.opened_at),
              coalesce(c.company_size_code,nullif(e.company_size,''),'00'),
              coalesce(c.company_size,CASE e.company_size
                WHEN '00' THEN 'NAO INFORMADO' WHEN '01' THEN 'MICRO EMPRESA'
                WHEN '03' THEN 'EMPRESA DE PEQUENO PORTE' WHEN '05' THEN 'DEMAIS'
                ELSE nullif(e.company_size,'') END,'NAO INFORMADO'),
              coalesce(e.share_capital,c.share_capital),c.legal_nature_code,nature.label,
              c.responsible_qualification_code,responsible.label,c.federative_entity,
              coalesce(e.primary_cnae,x.primary_cnae),cnae_ref.label,
              coalesce(e.secondary_cnaes,x.secondary_cnaes),
              coalesce(e.street_type,x.street_type),coalesce(e.street,x.street),
              coalesce(e.street_number,x.street_number),coalesce(e.address_extra,x.address_extra),
              coalesce(e.district,x.district),e.postal_code,e.municipality_code,e.municipality,e.uf,
              x.branch_type_code,x.foreign_city_name,x.country_code,country_ref.label,
              x.phone1_area_code,x.phone1,x.phone2_area_code,x.phone2,x.fax_area_code,x.fax,
              x.email,x.special_status,x.special_status_date,
              s.is_simples,s.simples_started_at,s.simples_ended_at,
              s.is_mei,s.mei_started_at,s.mei_ended_at,
              coalesce(b.branch_count,0),coalesce(b.active_branch_count,0),
              coalesce(p.partner_count,0),coalesce(p.partner_age_codes,ARRAY[]::text[]),
              e.is_active,e.dataset_version,e.updated_at
            FROM rfb_establishments e
            LEFT JOIN rfb_company_details c
              ON c.dataset_version=e.dataset_version AND c.cnpj_root=e.cnpj_root
            LEFT JOIN rfb_establishment_details x
              ON x.dataset_version=e.dataset_version AND x.cnpj=e.cnpj
            LEFT JOIN rfb_simples s
              ON s.dataset_version=e.dataset_version AND s.cnpj_root=e.cnpj_root
            LEFT JOIN rfb_company_branch_counts b
              ON b.dataset_version=e.dataset_version AND b.cnpj_root=e.cnpj_root
            LEFT JOIN {self.partner_summary} p
              ON p.dataset_version=e.dataset_version AND p.cnpj_root=e.cnpj_root
            LEFT JOIN rfb_aux_reference cnae_ref
              ON cnae_ref.dataset_version=e.dataset_version AND cnae_ref.kind='cnae'
             AND cnae_ref.code=coalesce(e.primary_cnae,x.primary_cnae)
            LEFT JOIN rfb_aux_reference nature
              ON nature.dataset_version=e.dataset_version AND nature.kind='legal_nature'
             AND nature.code=c.legal_nature_code
            LEFT JOIN rfb_aux_reference responsible
              ON responsible.dataset_version=e.dataset_version AND responsible.kind='qualification'
             AND responsible.code=c.responsible_qualification_code
            LEFT JOIN rfb_aux_reference status_reason
              ON status_reason.dataset_version=e.dataset_version AND status_reason.kind='status_reason'
             AND status_reason.code=x.registration_status_reason_code
            LEFT JOIN rfb_aux_reference country_ref
              ON country_ref.dataset_version=e.dataset_version AND country_ref.kind='country'
             AND country_ref.code=x.country_code
            WHERE e.dataset_version=%s{state_predicate}
        """, parameters)

    def build_state(self, state: str) -> None:
        state = state.upper()
        if state not in STATES:
            raise ValueError(f"UF invalida: {state}")
        if state in self.state().states_completed:
            LOGGER.info("UF %s ja preparada", state)
            return
        LOGGER.info("consolidando UF %s", state)
        self.connection.execute(
            sql.SQL("TRUNCATE {}").format(sql.Identifier(self._partition(state)))
        )
        self._insert_establishments(state)
        self.connection.execute("""
            UPDATE rfb_unified_builds
            SET states_completed=array_append(states_completed,%s),updated_at=now()
            WHERE version=%s AND NOT (%s=ANY(states_completed))
        """, (state, self.version, state))
        self.connection.commit()

    def build_all_states(self) -> None:
        state = self.state()
        if state.states_completed == set(STATES):
            LOGGER.info("todas as UFs ja foram preparadas")
            return
        # A unica passagem nacional deixa o PostgreSQL ler cada fonte auxiliar
        # uma vez e escolher hash/merge joins. Repetir a mesma consulta por UF
        # transformaria as tabelas auxiliares em centenas de milhoes de buscas
        # aleatorias, mesmo que cada particao de destino fosse pequena.
        LOGGER.info("consolidando todas as UFs em uma unica passagem")
        self.connection.execute(
            sql.SQL("TRUNCATE {}").format(sql.Identifier(self.main_next))
        )
        self.connection.execute(
            "UPDATE rfb_unified_builds SET states_completed=ARRAY[]::text[],updated_at=now() WHERE version=%s",
            (self.version,),
        )
        self.connection.commit()
        self.connection.execute("SET LOCAL work_mem='256MB'")
        self.connection.execute("SET LOCAL max_parallel_workers_per_gather=4")
        self._insert_establishments()
        self.connection.execute(
            "UPDATE rfb_unified_builds SET states_completed=%s,updated_at=now() WHERE version=%s",
            (list(STATES), self.version),
        )
        self.connection.commit()

    def create_indexes(self) -> None:
        if self.state().indexes_ready:
            LOGGER.info("indices ja preparados")
            return
        indexes = (
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_cnpj_idx ON {self.main_next}(cnpj)",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_root_idx ON {self.main_next}(cnpj_root,cnpj) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_cnae_idx ON {self.main_next}(primary_cnae,cnpj) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_location_idx ON {self.main_next}(municipality,postal_code,cnpj) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_capital_idx ON {self.main_next}(share_capital,cnpj) WHERE is_active AND share_capital IS NOT NULL",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_opened_idx ON {self.main_next}(opened_at,cnpj) WHERE is_active AND opened_at IS NOT NULL",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_size_idx ON {self.main_next}(company_size_code,cnpj) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_partner_count_idx ON {self.main_next}(partner_count,cnpj) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_branch_count_idx ON {self.main_next}(active_branch_count,cnpj) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_status_idx ON {self.main_next}(registration_status,cnpj)",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_secondary_cnaes_idx ON {self.main_next} USING gin(secondary_cnaes) WHERE is_active AND secondary_cnaes IS NOT NULL",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_partner_ages_idx ON {self.main_next} USING gin(partner_age_codes) WHERE is_active AND cardinality(partner_age_codes)>0",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_trade_trgm_idx ON {self.main_next} USING gin(normalized_trade_name gin_trgm_ops) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.main_next}_legal_trgm_idx ON {self.main_next} USING gin(normalized_legal_name gin_trgm_ops) WHERE is_active",
            f"CREATE INDEX IF NOT EXISTS {self.partners_next}_root_idx ON {self.partners_next}(cnpj_root,dataset_version)",
        )
        for statement in indexes:
            LOGGER.info("criando indice %s", statement.split()[5])
            self.connection.execute(statement)
            self.connection.commit()
        self.connection.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(self.main_next)))
        self.connection.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(self.partners_next)))
        self.connection.execute(
            "UPDATE rfb_unified_builds SET indexes_ready=true,updated_at=now() WHERE version=%s",
            (self.version,),
        )
        self.connection.commit()

    def validate(self) -> dict[str, Any]:
        state = self.state()
        if set(STATES) - state.states_completed:
            raise RuntimeError("todas as UFs precisam ser consolidadas antes da validacao")
        if not state.partners_ready or not state.indexes_ready:
            raise RuntimeError("socios e indices precisam estar prontos antes da validacao")
        source_rows = self.connection.execute(
            "SELECT count(*) AS rows FROM rfb_establishments WHERE dataset_version=%s", (self.version,)
        ).fetchone()["rows"]
        target_rows = self.connection.execute(
            f"SELECT count(*) AS rows FROM {self.main_next} WHERE dataset_version=%s", (self.version,)
        ).fetchone()["rows"]
        source_partners = self.connection.execute(
            "SELECT count(*) AS rows FROM rfb_partners WHERE dataset_version=%s", (self.version,)
        ).fetchone()["rows"]
        target_partners = self.connection.execute(
            f"SELECT count(*) AS rows FROM {self.partners_next} WHERE dataset_version=%s", (self.version,)
        ).fetchone()["rows"]
        per_state = self.connection.execute(f"""
            SELECT source.uf,source.rows AS source_rows,target.rows AS target_rows
            FROM (
              SELECT uf,count(*) AS rows FROM rfb_establishments
              WHERE dataset_version=%s GROUP BY uf
            ) source
            JOIN (
              SELECT uf,count(*) AS rows FROM {self.main_next}
              WHERE dataset_version=%s GROUP BY uf
            ) target USING(uf)
            WHERE source.rows<>target.rows
        """, (self.version, self.version)).fetchall()
        sampled_mismatches = self.connection.execute(f"""
            WITH sample AS (
              SELECT uf,cnpj FROM rfb_establishments TABLESAMPLE SYSTEM (0.02)
              WHERE dataset_version=%s LIMIT 25000
            )
            SELECT count(*) AS mismatches
            FROM sample s
            JOIN rfb_establishments old USING(uf,cnpj)
            JOIN {self.main_next} new USING(uf,cnpj)
            WHERE (coalesce(old.legal_name,''),coalesce(old.trade_name,''),old.registration_status,
                   coalesce(old.municipality,''),coalesce(old.postal_code,''))
              IS DISTINCT FROM
                  (coalesce(new.legal_name,''),coalesce(new.trade_name,''),new.registration_status,
                   coalesce(new.municipality,''),coalesce(new.postal_code,''))
        """, (self.version,)).fetchone()["mismatches"]
        validation = {
            "source_rows": int(source_rows),
            "target_rows": int(target_rows),
            "source_partners": int(source_partners),
            "target_partners": int(target_partners),
            "state_mismatches": [dict(row) for row in per_state],
            "sampled_core_mismatches": int(sampled_mismatches),
        }
        valid = (
            source_rows == target_rows
            and source_partners == target_partners
            and not per_state
            and sampled_mismatches == 0
        )
        self.connection.execute(
            """UPDATE rfb_unified_builds
               SET status=%s,validated=%s,validation=%s::jsonb,updated_at=now(),error=%s
               WHERE version=%s""",
            (
                "validated" if valid else "validation_failed",
                valid,
                json.dumps(validation),
                None if valid else "divergencia na validacao",
                self.version,
            ),
        )
        self.connection.commit()
        if not valid:
            raise RuntimeError(f"validacao falhou: {validation}")
        return validation

    def _metadata(self) -> tuple[dict[str, Any], dict[str, list[str]]]:
        reference_source = "rfb_aux_reference"
        direct_reference = f"rfb_unified_reference_build_{self.suffix}"
        direct_exists = self.connection.execute(
            "SELECT to_regclass(%s) AS relation", (f"public.{direct_reference}",)
        ).fetchone()["relation"]
        if direct_exists:
            reference_source = direct_reference
        rows = self.connection.execute(f"""
            SELECT kind,jsonb_object_agg(code,label ORDER BY code) AS labels
            FROM {reference_source} WHERE dataset_version=%s GROUP BY kind
        """, (self.version,)).fetchall()
        references = {row["kind"]: row["labels"] for row in rows}
        municipality_rows = self.connection.execute(f"""
            SELECT uf,jsonb_agg(DISTINCT municipality ORDER BY municipality) AS names
            FROM {self.main_next}
            WHERE dataset_version=%s AND is_active AND municipality IS NOT NULL
            GROUP BY uf
        """, (self.version,)).fetchall()
        municipalities = {row["uf"]: row["names"] for row in municipality_rows}
        return references, municipalities

    def publish(self) -> None:
        if not self.state().validated:
            raise RuntimeError("a construcao precisa ser validada antes da publicacao")
        references, municipalities = self._metadata()
        legacy_main = f"rfb_establishments_legacy_{self.suffix}"
        legacy_partners = f"rfb_partners_legacy_{self.suffix}"
        self._copy_select_grants("rfb_establishments", self.main_next)
        self._copy_select_grants("rfb_partners", self.partners_next)
        with self.connection.transaction():
            previous = self.connection.execute(
                "SELECT version FROM dataset_versions WHERE is_current AND status='ready' LIMIT 1"
            ).fetchone()
            previous_version = previous["version"] if previous else None
            for relation in (legacy_main, legacy_partners):
                exists = self.connection.execute(
                    "SELECT to_regclass(%s) AS relation", (f"public.{relation}",)
                ).fetchone()["relation"]
                if exists:
                    raise RuntimeError(f"a relacao de rollback {relation} ja existe")
            self.connection.execute(f"ALTER TABLE rfb_establishments RENAME TO {legacy_main}")
            self.connection.execute(f"ALTER TABLE {self.main_next} RENAME TO rfb_establishments")
            self.connection.execute(f"ALTER TABLE rfb_partners RENAME TO {legacy_partners}")
            self.connection.execute(f"ALTER TABLE {self.partners_next} RENAME TO rfb_partners")
            self._refresh_partner_view()
            counts = self.connection.execute("""
                SELECT count(*) FILTER (WHERE is_active) AS active_count,
                       count(*) FILTER (WHERE NOT is_active) AS inactive_count
                FROM rfb_establishments WHERE dataset_version=%s
            """, (self.version,)).fetchone()
            self.connection.execute(
                "UPDATE dataset_versions SET is_current=false WHERE version<>%s",
                (self.version,),
            )
            self.connection.execute("""
                UPDATE dataset_versions
                SET status='ready',is_current=true,imported_at=now(),
                    active_count=%s,inactive_count=%s,
                    metadata=metadata || jsonb_build_object(
                      'unified_search','ready',
                      'unified_search_published_at',now(),
                      'unified_search_legacy_establishments',%s,
                      'unified_search_legacy_partners',%s,
                      'unified_search_previous_version',%s,
                      'reference_labels',%s::jsonb,
                      'municipalities_by_uf',%s::jsonb
                    )
                WHERE version=%s
            """, (
                counts["active_count"],
                counts["inactive_count"],
                legacy_main,
                legacy_partners,
                previous_version,
                json.dumps(references, ensure_ascii=False),
                json.dumps(municipalities, ensure_ascii=False),
                self.version,
            ))
            self.connection.execute("""
                UPDATE rfb_unified_builds
                SET status='published',published=true,updated_at=now()
                WHERE version=%s
            """, (self.version,))
        LOGGER.info("tabela unificada publicada; rollback em %s e %s", legacy_main, legacy_partners)

    def rollback(self) -> None:
        row = self.connection.execute("""
            SELECT metadata->>'unified_search_legacy_establishments' AS legacy_main,
                   metadata->>'unified_search_legacy_partners' AS legacy_partners,
                   metadata->>'unified_search_previous_version' AS previous_version
            FROM dataset_versions WHERE version=%s
        """, (self.version,)).fetchone()
        if not row or not row["legacy_main"] or not row["legacy_partners"]:
            raise RuntimeError("nao ha tabelas de rollback registradas")
        failed_main = f"rfb_establishments_unified_failed_{self.version.replace('-', '_')}"
        failed_partners = f"rfb_partners_unified_failed_{self.version.replace('-', '_')}"
        with self.connection.transaction():
            self.connection.execute(f"ALTER TABLE rfb_establishments RENAME TO {failed_main}")
            self.connection.execute(f"ALTER TABLE {row['legacy_main']} RENAME TO rfb_establishments")
            self.connection.execute(f"ALTER TABLE rfb_partners RENAME TO {failed_partners}")
            self.connection.execute(f"ALTER TABLE {row['legacy_partners']} RENAME TO rfb_partners")
            self._refresh_partner_view()
            self.connection.execute(
                "UPDATE dataset_versions SET is_current=false WHERE version=%s",
                (self.version,),
            )
            if row["previous_version"]:
                self.connection.execute(
                    "UPDATE dataset_versions SET is_current=true,status='ready' WHERE version=%s",
                    (row["previous_version"],),
                )
            self.connection.execute("""
                UPDATE dataset_versions
                SET metadata=(metadata-'unified_search') || jsonb_build_object('unified_search','rolled_back')
                WHERE version=%s
            """, (self.version,))

    def retire_legacy(self) -> None:
        row = self.connection.execute("""
            SELECT metadata->>'unified_search' AS status,
                   metadata->>'unified_search_legacy_establishments' AS legacy_main,
                   metadata->>'unified_search_legacy_partners' AS legacy_partners,
                   (metadata->>'unified_search_published_at')::timestamptz AS published_at
            FROM dataset_versions WHERE version=%s
        """, (self.version,)).fetchone()
        if not row or row["status"] != "ready" or not row["published_at"]:
            raise RuntimeError("a versao unificada ainda nao esta publicada")
        age = self.connection.execute(
            "SELECT now()-%s::timestamptz AS age", (row["published_at"],)
        ).fetchone()["age"]
        if age.days < 7:
            raise RuntimeError("o periodo minimo de rollback de sete dias ainda nao terminou")
        views = (
            "rfb_current_company_details", "rfb_current_establishment_details",
            "rfb_current_simples", "rfb_current_company_branch_counts",
            "rfb_current_aux_reference",
        )
        tables = (
            row["legacy_main"], row["legacy_partners"], "rfb_company_details",
            "rfb_establishment_details", "rfb_simples", "rfb_company_branch_counts",
            "rfb_aux_reference",
        )
        with self.connection.transaction():
            for view in views:
                self.connection.execute(
                    sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(view))
                )
            for table in tables:
                if table:
                    self.connection.execute(
                        sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table))
                    )
            self.connection.execute("""
                UPDATE dataset_versions
                SET metadata=metadata || jsonb_build_object(
                    'unified_search_legacy_retired_at',now()
                )
                WHERE version=%s
            """, (self.version,))
            self.connection.execute("""
                UPDATE rfb_unified_builds SET status='retired',updated_at=now()
                WHERE version=%s
            """, (self.version,))

    def discard_incomplete(self) -> None:
        """Remove only disposable shadow relations for an unpublished version."""
        builds_exists = self.connection.execute(
            "SELECT to_regclass('public.rfb_unified_builds') AS relation"
        ).fetchone()["relation"]
        row = None
        if builds_exists:
            row = self.connection.execute(
                "SELECT published FROM rfb_unified_builds WHERE version=%s", (self.version,)
            ).fetchone()
        if row and row["published"]:
            raise RuntimeError("uma construcao publicada nao pode ser descartada")
        relations = (
            self.main_next,
            self.partners_next,
            self.partner_summary,
            f"rfb_unified_company_build_{self.suffix}",
            f"rfb_unified_simples_build_{self.suffix}",
            f"rfb_unified_reference_build_{self.suffix}",
            f"rfb_unified_branch_rows_build_{self.suffix}",
            f"rfb_unified_branch_summary_build_{self.suffix}",
        )
        for relation in relations:
            self.connection.execute(
                sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(relation))
            )
        if builds_exists:
            self.connection.execute("DELETE FROM rfb_unified_builds WHERE version=%s", (self.version,))
        for tracking in ("rfb_unified_import_files", "rfb_unified_imports"):
            exists = self.connection.execute(
                "SELECT to_regclass(%s) AS relation", (f"public.{tracking}",)
            ).fetchone()["relation"]
            if exists:
                self.connection.execute(
                    sql.SQL("DELETE FROM {} WHERE version=%s").format(sql.Identifier(tracking)),
                    (self.version,),
                )
        self.connection.commit()
        LOGGER.info("construcao incompleta %s descartada", self.version)


def main() -> None:
    parser = argparse.ArgumentParser(description="Constroi a tabela unificada de busca da Receita")
    parser.add_argument("--version", required=True)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--state", choices=STATES)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--retire-legacy", action="store_true")
    parser.add_argument("--discard-incomplete", action="store_true")
    args = parser.parse_args()
    dsn = os.getenv("ADMIN_POSTGRES_DSN") or os.getenv("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("configure ADMIN_POSTGRES_DSN sem gravar credenciais no repositorio")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as connection:
        connection.execute("SET statement_timeout=0")
        builder = UnifiedSearchBuilder(connection, args.version)
        builder.lock()
        try:
            if args.discard_incomplete:
                builder.discard_incomplete()
                return
            if args.rollback:
                builder.rollback()
                return
            if args.retire_legacy:
                builder.retire_legacy()
                return
            if args.validate_only:
                print(json.dumps(builder.validate(), ensure_ascii=False))
                if args.publish:
                    builder.publish()
                return
            builder.initialize(restart=args.restart)
            if args.prepare_only:
                return
            builder.build_partners()
            if args.state:
                builder.build_state(args.state)
            else:
                builder.build_all_states()
                builder.create_indexes()
                print(json.dumps(builder.validate(), ensure_ascii=False))
                if args.publish:
                    builder.publish()
        except Exception as error:
            connection.rollback()
            try:
                connection.execute(
                    "UPDATE rfb_unified_builds SET status='failed',error=%s,updated_at=now() WHERE version=%s",
                    (type(error).__name__, args.version),
                )
                connection.commit()
            except Exception:
                connection.rollback()
            raise
        finally:
            builder.unlock()


if __name__ == "__main__":
    main()
