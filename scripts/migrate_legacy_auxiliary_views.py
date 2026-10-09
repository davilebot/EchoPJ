#!/usr/bin/env python3
"""Replace duplicated auxiliary storage with views over the unified dataset.

The migration is deliberately staged:

* ``--prepare`` builds the matrix-root index and candidate views;
* ``--cutover`` keeps the old tables as explicitly named backups;
* ``--rollback`` restores those backups if either application smoke test fails;
* ``--finalize`` removes the backups only after the smoke tests pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


LARGE_RELATIONS: dict[str, tuple[str, ...]] = {
    "rfb_company_details": (
        "dataset_version", "cnpj_root", "legal_nature_code",
        "responsible_qualification_code", "company_size_code", "company_size",
        "share_capital", "federative_entity",
    ),
    "rfb_establishment_details": (
        "dataset_version", "cnpj", "branch_type_code", "registration_status_reason_code",
        "foreign_city_name", "country_code", "opened_at", "primary_cnae",
        "secondary_cnaes", "street_type", "street", "street_number", "address_extra",
        "district", "phone1_area_code", "phone1", "phone2_area_code", "phone2",
        "fax_area_code", "fax", "email", "special_status", "special_status_date",
    ),
    "rfb_simples": (
        "dataset_version", "cnpj_root", "is_simples", "simples_started_at",
        "simples_ended_at", "is_mei", "mei_started_at", "mei_ended_at",
    ),
    "rfb_company_branch_counts": (
        "dataset_version", "cnpj_root", "branch_count", "active_branch_count",
    ),
}
CURRENT_VIEWS = {
    "rfb_current_company_details": "rfb_company_details",
    "rfb_current_establishment_details": "rfb_establishment_details",
    "rfb_current_simples": "rfb_simples",
    "rfb_current_company_branch_counts": "rfb_company_branch_counts",
}
SAFE_VERSION = re.compile(r"^\d{4}-\d{2}$")


def _candidate_name(name: str) -> str:
    return f"{name}_unified_compat"


def _backup_name(name: str, version: str) -> str:
    return f"{name}_storage_backup_{version.replace('-', '_')}"


def _select_sql(name: str) -> str:
    columns = ",".join(LARGE_RELATIONS[name])
    matrix_only = name in {
        "rfb_company_details", "rfb_simples", "rfb_company_branch_counts",
    }
    where = " WHERE branch_type_code='1'" if matrix_only else ""
    return f"SELECT {columns} FROM rfb_establishments{where}"


def _relation_kind(connection: psycopg.Connection[Any], name: str) -> str | None:
    row = connection.execute(
        "SELECT relkind FROM pg_class WHERE oid=to_regclass(%s)", (f"public.{name}",)
    ).fetchone()
    return row["relkind"] if row else None


def _grantees(connection: psycopg.Connection[Any], relation: str) -> list[str]:
    rows = connection.execute("""
        SELECT DISTINCT grantee FROM information_schema.role_table_grants
        WHERE table_schema='public' AND table_name=%s AND privilege_type='SELECT'
          AND grantee<>current_user
    """, (relation,)).fetchall()
    return [row["grantee"] for row in rows]


def _grant_select(
    connection: psycopg.Connection[Any], relation: str, grantees: list[str]
) -> None:
    for grantee in grantees:
        connection.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.Identifier(relation), sql.Identifier(grantee),
            )
        )


def current_version(connection: psycopg.Connection[Any]) -> str:
    row = connection.execute("""
        SELECT version FROM dataset_versions
        WHERE is_current AND status='ready' AND metadata->>'unified_search'='ready'
        LIMIT 1
    """).fetchone()
    if not row or not SAFE_VERSION.fullmatch(row["version"]):
        raise RuntimeError("a base unificada atual nao esta pronta")
    return row["version"]


def prepare(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    version = current_version(connection)
    for name in LARGE_RELATIONS:
        if _relation_kind(connection, name) not in {"r", "p"}:
            raise RuntimeError(f"{name} nao e uma tabela de armazenamento")

    connection.execute("""
        CREATE INDEX IF NOT EXISTS rfb_establishments_matrix_root_idx
        ON rfb_establishments(cnpj_root)
        WHERE branch_type_code='1'
    """)
    connection.commit()
    for name in LARGE_RELATIONS:
        candidate = _candidate_name(name)
        connection.execute(
            sql.SQL("CREATE OR REPLACE VIEW {} AS " + _select_sql(name)).format(
                sql.Identifier(candidate)
            )
        )
        _grant_select(connection, candidate, _grantees(connection, name))
    connection.commit()

    samples: dict[str, int] = {}
    for name in LARGE_RELATIONS:
        candidate = _candidate_name(name)
        row = connection.execute(
            sql.SQL("SELECT count(*) AS rows FROM (SELECT 1 FROM {} LIMIT 1000) sample").format(
                sql.Identifier(candidate)
            )
        ).fetchone()
        samples[name] = int(row["rows"])
        if samples[name] < 1000:
            raise RuntimeError(f"amostra insuficiente na view {candidate}: {samples[name]}")
    return {"action": "prepared", "version": version, "samples": samples}


def _create_current_views(
    connection: psycopg.Connection[Any], grants: dict[str, list[str]]
) -> None:
    for view, source in CURRENT_VIEWS.items():
        columns = ",".join(f"d.{column}" for column in LARGE_RELATIONS[source])
        connection.execute(
            sql.SQL("CREATE VIEW {} AS SELECT " + columns + " FROM {} d "
                    "JOIN dataset_versions v ON v.version=d.dataset_version "
                    "WHERE v.is_current AND v.status='ready'").format(
                sql.Identifier(view), sql.Identifier(source)
            )
        )
        _grant_select(connection, view, grants.get(view, []))


def cutover(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    version = current_version(connection)
    grants = {name: _grantees(connection, name) for name in (*LARGE_RELATIONS, *CURRENT_VIEWS)}
    for name in LARGE_RELATIONS:
        if _relation_kind(connection, _candidate_name(name)) != "v":
            raise RuntimeError(f"execute --prepare antes do cutover: {_candidate_name(name)}")
        if _relation_kind(connection, _backup_name(name, version)):
            raise RuntimeError(f"backup de cutover ja existe para {name}")

    with connection.transaction():
        for view in CURRENT_VIEWS:
            connection.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(view)))
        for name in LARGE_RELATIONS:
            connection.execute(
                sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                    sql.Identifier(name), sql.Identifier(_backup_name(name, version)),
                )
            )
            connection.execute(
                sql.SQL("ALTER VIEW {} RENAME TO {}").format(
                    sql.Identifier(_candidate_name(name)), sql.Identifier(name),
                )
            )
            _grant_select(connection, name, grants.get(name, []))
        _create_current_views(connection, grants)
    return {
        "action": "cutover",
        "version": version,
        "backups": [_backup_name(name, version) for name in LARGE_RELATIONS],
    }


def rollback(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    version = current_version(connection)
    grants = {name: _grantees(connection, name) for name in (*LARGE_RELATIONS, *CURRENT_VIEWS)}
    for name in LARGE_RELATIONS:
        if _relation_kind(connection, name) != "v":
            raise RuntimeError(f"{name} nao esta em modo de compatibilidade")
        if _relation_kind(connection, _backup_name(name, version)) not in {"r", "p"}:
            raise RuntimeError(f"backup ausente: {_backup_name(name, version)}")
    with connection.transaction():
        for view in CURRENT_VIEWS:
            connection.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(view)))
        for name in LARGE_RELATIONS:
            connection.execute(sql.SQL("DROP VIEW {}").format(sql.Identifier(name)))
            connection.execute(
                sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                    sql.Identifier(_backup_name(name, version)), sql.Identifier(name),
                )
            )
            _grant_select(connection, name, grants.get(name, []))
        for view, source in CURRENT_VIEWS.items():
            columns = ",".join(f"d.{column}" for column in LARGE_RELATIONS[source])
            connection.execute(
                sql.SQL("CREATE VIEW {} AS SELECT " + columns + " FROM {} d "
                        "JOIN rfb_aux_datasets v ON v.version=d.dataset_version "
                        "AND v.status='current'").format(
                    sql.Identifier(view), sql.Identifier(source)
                )
            )
            _grant_select(connection, view, grants.get(view, []))
    return {"action": "rolled_back", "version": version}


def finalize(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    version = current_version(connection)
    backups = [_backup_name(name, version) for name in LARGE_RELATIONS]
    for name in LARGE_RELATIONS:
        if _relation_kind(connection, name) != "v":
            raise RuntimeError(f"{name} nao e uma view; finalizacao recusada")
        if _relation_kind(connection, _backup_name(name, version)) not in {"r", "p"}:
            raise RuntimeError(f"backup ausente: {_backup_name(name, version)}")
    sizes = {
        backup: int(connection.execute(
            "SELECT pg_total_relation_size(%s::regclass) AS bytes", (f"public.{backup}",)
        ).fetchone()["bytes"])
        for backup in backups
    }
    with connection.transaction():
        for backup in backups:
            connection.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(backup)))
    return {"action": "finalized", "version": version, "freed_bytes": sum(sizes.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra auxiliares duplicadas para views unificadas")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--cutover", action="store_true")
    action.add_argument("--rollback", action="store_true")
    action.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    dsn = os.getenv("ADMIN_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("configure ADMIN_POSTGRES_DSN fora do repositorio")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as connection:
        connection.execute("SET statement_timeout=0")
        if args.prepare:
            result = prepare(connection)
        elif args.cutover:
            result = cutover(connection)
        elif args.rollback:
            result = rollback(connection)
        else:
            result = finalize(connection)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
