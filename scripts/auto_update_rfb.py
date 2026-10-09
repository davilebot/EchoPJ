#!/usr/bin/env python3
"""Detect, validate, import and publish the next Receita CNPJ release.

The command is designed for a daily systemd timer.  A missing release, an
unchanged current version or a manifest that is still stabilizing are normal
successful outcomes.  The live relations are replaced only by the existing
atomic publication path after the complete import validates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from plataforma_receita.rfb_manifest import (
    Manifest,
    discover_official_manifest,
    manifest_payload,
)
try:
    from scripts.import_unified_dataset import ImportSettings, UnifiedDatasetImporter
except ModuleNotFoundError:  # Execucao direta: python scripts/auto_update_rfb.py
    from import_unified_dataset import ImportSettings, UnifiedDatasetImporter


LOGGER = logging.getLogger("rfb-auto-update")
GIB = 1024**3
SHARDED_KINDS = ("companies", "establishments", "partners")
REFERENCE_KINDS = {
    "reference_cnaes",
    "reference_countries",
    "reference_legal_natures",
    "reference_municipalities",
    "reference_qualifications",
    "reference_status_reasons",
}
ROLLBACK_RELATION = re.compile(r"^rfb_(?:establishments|partners)_legacy_\d{4}_\d{2}$")


class ManifestNotReady(RuntimeError):
    """The official folder exists but is not safe to import yet."""


class CapacityUnavailable(RuntimeError):
    """The host cannot safely hold the next shadow dataset."""


@dataclass(frozen=True)
class AutoUpdateSettings:
    share_url: str
    state_path: Path
    manifest_directory: Path
    cache_directory: Path
    stability_hours: float = 18.0
    minimum_shards: int = 10
    minimum_free_gb: float = 12.0
    maximum_database_gb: float = 175.0
    expected_growth_factor: float = 1.10
    retire_rollbacks: bool = False
    rollback_days: int = 7
    job_databases: tuple[Path, ...] = ()
    readiness_urls: tuple[str, ...] = ()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def month_after(version: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})", version)
    if not match:
        raise ValueError(f"versao invalida: {version}")
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise ValueError(f"mes invalido: {version}")
    month += 1
    if month == 13:
        year += 1
        month = 1
    return f"{year:04d}-{month:02d}"


def _shard_numbers(manifest: Manifest, kind: str) -> list[int]:
    numbers: list[int] = []
    for entry in manifest.files:
        if entry.kind != kind:
            continue
        match = re.search(r"(\d+)\.zip$", entry.name, flags=re.IGNORECASE)
        if not match:
            raise ManifestNotReady(f"nome inesperado em {kind}: {entry.name}")
        numbers.append(int(match.group(1)))
    return sorted(numbers)


def validate_release_manifest(manifest: Manifest, *, minimum_shards: int = 10) -> dict[str, Any]:
    """Require the complete monthly shape, not merely one file of each kind."""
    if any(entry.size is None or entry.size <= 0 for entry in manifest.files):
        raise ManifestNotReady("todos os arquivos precisam ter tamanho oficial positivo")

    shard_sets = {kind: _shard_numbers(manifest, kind) for kind in SHARDED_KINDS}
    for kind, numbers in shard_sets.items():
        if len(numbers) < minimum_shards:
            raise ManifestNotReady(
                f"{kind} ainda tem {len(numbers)} fragmentos; minimo esperado: {minimum_shards}"
            )
        if numbers != list(range(numbers[-1] + 1)):
            raise ManifestNotReady(f"fragmentos de {kind} nao sao contiguos desde zero")
    if len({tuple(numbers) for numbers in shard_sets.values()}) != 1:
        raise ManifestNotReady("Empresas, Estabelecimentos e Socios divergem em fragmentos")

    counts = {kind: sum(entry.kind == kind for entry in manifest.files) for kind in REFERENCE_KINDS}
    missing_references = sorted(kind for kind, count in counts.items() if count != 1)
    if missing_references:
        raise ManifestNotReady(
            "dicionarios ausentes ou duplicados: " + ", ".join(missing_references)
        )
    simples = sum(entry.kind == "simples" for entry in manifest.files)
    if simples != 1:
        raise ManifestNotReady(f"esperado um arquivo Simples; encontrados: {simples}")

    return {
        "files": len(manifest.files),
        "known_download_bytes": sum(entry.size or 0 for entry in manifest.files),
        "shards": len(shard_sets["companies"]),
        "references": len(REFERENCE_KINDS),
    }


def manifest_fingerprint(manifest: Manifest) -> str:
    payload = [
        [entry.kind, entry.name, entry.size, entry.url]
        for entry in sorted(manifest.files, key=lambda item: (item.kind, item.name))
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def stable_since(
    previous: dict[str, Any],
    *,
    version: str,
    fingerprint: str,
    now: datetime,
    stability_hours: float,
) -> tuple[bool, datetime]:
    first_seen = now
    if previous.get("target_version") == version and previous.get("manifest_fingerprint") == fingerprint:
        try:
            first_seen = datetime.fromisoformat(str(previous["manifest_first_seen_at"]))
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)
            first_seen = first_seen.astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            first_seen = now
    return now - first_seen >= timedelta(hours=max(0.0, stability_hours)), first_seen


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def update_status(path: Path, status: str, **fields: Any) -> dict[str, Any]:
    payload = {**read_json(path), **fields, "status": status, "checked_at": utc_now().isoformat()}
    write_json(path, payload)
    LOGGER.info("estado=%s versao_atual=%s alvo=%s", status, payload.get("current_version"), payload.get("target_version"))
    return payload


def active_jobs(paths: Iterable[Path]) -> dict[str, int]:
    result: dict[str, int] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
                }
                if "status" not in columns:
                    continue
                count = connection.execute(
                    "SELECT count(*) FROM jobs WHERE lower(status) IN ('queued','running')"
                ).fetchone()[0]
            if count:
                result[str(path)] = int(count)
        except sqlite3.Error as error:
            raise RuntimeError(f"nao foi possivel auditar a fila {path}: {error}") from error
    return result


def current_dataset_version(connection: psycopg.Connection[Any]) -> str:
    row = connection.execute(
        "SELECT version FROM dataset_versions WHERE is_current AND status='ready' "
        "ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("nenhuma versao atual e pronta foi encontrada")
    return str(row["version"])


def live_relation_bytes(connection: psycopg.Connection[Any]) -> int:
    row = connection.execute("""
        SELECT
          coalesce((
            SELECT sum(pg_total_relation_size(relid))
            FROM pg_partition_tree('rfb_establishments'::regclass)
          ),0)
          + pg_total_relation_size('rfb_partners'::regclass) AS bytes
    """).fetchone()
    return int(row["bytes"])


def ensure_capacity(
    connection: psycopg.Connection[Any],
    settings: AutoUpdateSettings,
    manifest_summary: dict[str, Any],
) -> dict[str, int]:
    free_bytes = shutil.disk_usage(settings.cache_directory).free
    live_bytes = live_relation_bytes(connection)
    required_bytes = (
        int(live_bytes * max(1.0, settings.expected_growth_factor))
        + int(manifest_summary["known_download_bytes"])
        + int(settings.minimum_free_gb * GIB)
    )
    if free_bytes < required_bytes:
        raise CapacityUnavailable(
            f"espaco livre {free_bytes / GIB:.1f} GB; necessario {required_bytes / GIB:.1f} GB "
            "para shadow, downloads e margem"
        )
    return {"free_bytes": free_bytes, "live_bytes": live_bytes, "required_bytes": required_bytes}


def retire_expired_rollback(
    connection: psycopg.Connection[Any], *, version: str, minimum_days: int
) -> list[str]:
    row = connection.execute("""
        SELECT metadata->>'unified_search_legacy_establishments' AS legacy_main,
               metadata->>'unified_search_legacy_partners' AS legacy_partners,
               metadata->>'unified_search_legacy_retired_at' AS retired_at,
               (metadata->>'unified_search_published_at')::timestamptz AS published_at
        FROM dataset_versions WHERE version=%s AND is_current AND status='ready'
    """, (version,)).fetchone()
    if not row or row["retired_at"] or not row["published_at"]:
        return []
    if utc_now() - row["published_at"].astimezone(timezone.utc) < timedelta(days=minimum_days):
        return []

    relations = [row["legacy_main"], row["legacy_partners"]]
    if any(not relation or not ROLLBACK_RELATION.fullmatch(relation) for relation in relations):
        raise RuntimeError("nomes de rollback invalidos; limpeza recusada")
    with connection.transaction():
        for relation in relations:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(relation)))
        connection.execute("""
            UPDATE dataset_versions
            SET metadata=metadata || jsonb_build_object('unified_search_legacy_retired_at',now())
            WHERE version=%s
        """, (version,))
        connection.execute(
            "UPDATE rfb_unified_builds SET status='retired',updated_at=now() WHERE version=%s",
            (version,),
        )
    return relations


def verify_readiness(urls: Iterable[str], expected_version: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for url in urls:
        request = urllib.request.Request(url, headers={"User-Agent": "EchoPJs-RFB-Updater/1.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        reported = payload.get("dataset_version")
        if reported and reported != expected_version:
            raise RuntimeError(f"{url} ainda informa a versao {reported}")
        if payload.get("ready") is False or payload.get("status") == "degraded":
            raise RuntimeError(f"{url} informou servico degradado")
        results.append({"url": url, "status": payload.get("status"), "dataset_version": reported})
    return results


def sync_legacy_compatibility(
    connection: psycopg.Connection[Any], manifest: Manifest
) -> dict[str, int]:
    """Keep the active legacy application on the consolidated monthly data.

    The large legacy relations are compatibility views.  These three small
    control/reference tables are retained so the older application continues
    to advertise the rich fields after the current version changes.
    """
    required = ("rfb_aux_datasets", "rfb_aux_import_files", "rfb_aux_reference")
    existing = connection.execute(
        "SELECT relname FROM pg_class WHERE relname=ANY(%s)", (list(required),)
    ).fetchall()
    if {row["relname"] for row in existing} != set(required):
        return {"files": 0, "references": 0}

    connection.execute(
        "UPDATE rfb_aux_datasets SET status='ready' WHERE status='current' AND version<>%s",
        (manifest.version,),
    )
    connection.execute("""
        INSERT INTO rfb_aux_datasets(
          version,source_url,status,started_at,published_at,completed_at,error,metadata
        ) VALUES (%s,%s,'current',now(),now(),now(),NULL,
                  jsonb_build_object('branch_counts','ready','unified_compatibility',true))
        ON CONFLICT(version) DO UPDATE SET
          source_url=excluded.source_url,status='current',published_at=now(),completed_at=now(),
          error=NULL,metadata=rfb_aux_datasets.metadata || excluded.metadata
    """, (manifest.version, manifest.source_url))
    connection.execute(
        "DELETE FROM rfb_aux_import_files WHERE dataset_version=%s", (manifest.version,)
    )
    connection.executemany("""
        INSERT INTO rfb_aux_import_files(
          dataset_version,file_name,kind,source_url,status,source_rows_processed,
          rows_loaded,bytes_downloaded,started_at,completed_at,error
        ) VALUES (%s,%s,%s,%s,'completed',0,0,%s,now(),now(),NULL)
    """, [
        (manifest.version, entry.name, entry.kind, entry.url, entry.size or 0)
        for entry in manifest.files
    ])
    references = connection.execute("""
        INSERT INTO rfb_aux_reference(dataset_version,kind,code,label)
        SELECT d.version,k.key,c.key,c.value #>> '{}'
        FROM dataset_versions d
        CROSS JOIN LATERAL jsonb_each(d.metadata->'reference_labels') AS k
        CROSS JOIN LATERAL jsonb_each(k.value) AS c
        WHERE d.version=%s
        ON CONFLICT(dataset_version,kind,code) DO UPDATE SET label=excluded.label
        RETURNING 1
    """, (manifest.version,)).fetchall()
    connection.commit()
    return {"files": len(manifest.files), "references": len(references)}


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def settings_from_environment() -> AutoUpdateSettings:
    share_url = os.getenv("RFB_OFFICIAL_SHARE_URL", "").strip()
    if not share_url:
        raise RuntimeError("configure RFB_OFFICIAL_SHARE_URL")
    job_paths = tuple(
        Path(value.strip())
        for value in os.getenv("RFB_JOB_DATABASE_PATHS", "").split(",")
        if value.strip()
    )
    readiness_urls = tuple(
        value.strip()
        for value in os.getenv("RFB_READINESS_URLS", "").split(",")
        if value.strip()
    )
    return AutoUpdateSettings(
        share_url=share_url,
        state_path=Path(os.getenv("RFB_UPDATE_STATUS_PATH", "/state/status.json")),
        manifest_directory=Path(os.getenv("RFB_UPDATE_MANIFEST_DIR", "/state/manifests")),
        cache_directory=Path(os.getenv("RFB_UPDATE_CACHE_DIR", "/cache")),
        stability_hours=float(os.getenv("RFB_MANIFEST_STABILITY_HOURS", "18")),
        minimum_shards=int(os.getenv("RFB_MINIMUM_SHARDS", "10")),
        minimum_free_gb=float(os.getenv("RFB_MINIMUM_FREE_GB", "12")),
        maximum_database_gb=float(os.getenv("RFB_MAXIMUM_DATABASE_GB", "175")),
        expected_growth_factor=float(os.getenv("RFB_EXPECTED_GROWTH_FACTOR", "1.10")),
        retire_rollbacks=_bool_env("RFB_AUTO_RETIRE_ROLLBACKS", False),
        rollback_days=int(os.getenv("RFB_ROLLBACK_DAYS", "7")),
        job_databases=job_paths,
        readiness_urls=readiness_urls,
    )


def run(*, check_only: bool = False, force_stable: bool = False) -> dict[str, Any]:
    settings = settings_from_environment()
    settings.state_path.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_directory.mkdir(parents=True, exist_ok=True)
    settings.cache_directory.mkdir(parents=True, exist_ok=True)
    previous = read_json(settings.state_path)
    dsn = os.getenv("ADMIN_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("configure ADMIN_POSTGRES_DSN fora do repositorio")

    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as connection:
        connection.execute("SET statement_timeout=0")
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtext('echopjs-rfb-auto-update')) AS locked"
        ).fetchone()["locked"]
        if not locked:
            return update_status(settings.state_path, "already_running")
        try:
            current = current_dataset_version(connection)
            retired: list[str] = []
            if settings.retire_rollbacks:
                retired = retire_expired_rollback(
                    connection, version=current, minimum_days=settings.rollback_days
                )
            target = month_after(current)
            base_status = {
                "current_version": current,
                "target_version": target,
                "retired_relations": retired,
            }
            try:
                manifest = discover_official_manifest(target, settings.share_url)
            except urllib.error.HTTPError as error:
                if error.code in {404, 405}:
                    return update_status(
                        settings.state_path, "waiting_for_release", **base_status,
                        detail="A proxima competencia ainda nao foi publicada pela Receita.",
                    )
                raise
            except ValueError as error:
                return update_status(
                    settings.state_path, "manifest_incomplete", **base_status,
                    detail=f"A pasta existe, mas o manifesto ainda esta incompleto: {error}",
                )
            try:
                summary = validate_release_manifest(
                    manifest, minimum_shards=settings.minimum_shards
                )
            except ManifestNotReady as error:
                return update_status(
                    settings.state_path, "manifest_incomplete", **base_status,
                    detail=str(error),
                )
            fingerprint = manifest_fingerprint(manifest)
            now = utc_now()
            stable, first_seen = stable_since(
                previous,
                version=target,
                fingerprint=fingerprint,
                now=now,
                stability_hours=0 if force_stable else settings.stability_hours,
            )
            manifest_path = settings.manifest_directory / f"rfb-{target}-official-manifest.json"
            write_json(manifest_path, manifest_payload(manifest))
            manifest_status = {
                **base_status,
                "manifest_path": str(manifest_path),
                "manifest_fingerprint": fingerprint,
                "manifest_first_seen_at": first_seen.isoformat(),
                "manifest": summary,
            }
            if check_only:
                return update_status(settings.state_path, "check_complete", **manifest_status)
            if not stable:
                return update_status(
                    settings.state_path, "manifest_stabilizing", **manifest_status,
                    detail=f"Manifesto completo; aguardando {settings.stability_hours:g} horas sem alteracao.",
                )
            jobs = active_jobs(settings.job_databases)
            if jobs:
                return update_status(
                    settings.state_path, "waiting_for_idle", **manifest_status,
                    active_jobs=jobs,
                    detail="A carga aguardara as filas atuais terminarem.",
                )
            capacity = ensure_capacity(connection, settings, summary)
            update_status(
                settings.state_path, "importing", **manifest_status, capacity=capacity,
                import_started_at=now.isoformat(), detail="Carga resumivel iniciada.",
            )
            importer = UnifiedDatasetImporter(
                connection,
                manifest,
                ImportSettings(
                    cache_directory=settings.cache_directory,
                    minimum_free_gb=settings.minimum_free_gb,
                    maximum_database_gb=settings.maximum_database_gb,
                ),
            )
            importer.builder.lock()
            try:
                validation = importer.run(publish=True)
            finally:
                importer.builder.unlock()
            published = current_dataset_version(connection)
            if published != target:
                raise RuntimeError(f"publicacao terminou, mas a versao atual e {published}")
            legacy_compatibility = sync_legacy_compatibility(connection, manifest)
            readiness = verify_readiness(settings.readiness_urls, target)
            return update_status(
                settings.state_path, "published", **manifest_status,
                validation=validation,
                legacy_compatibility=legacy_compatibility,
                readiness=readiness,
                published_at=utc_now().isoformat(),
                detail="Nova competencia validada e publicada atomicamente.",
            )
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext('echopjs-rfb-auto-update'))")


def main() -> None:
    parser = argparse.ArgumentParser(description="Atualiza automaticamente a base mensal da Receita")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--force-stable",
        action="store_true",
        help="somente para homologacao controlada; ignora a janela de estabilidade",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        result = run(check_only=args.check_only, force_stable=args.force_stable)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        try:
            settings = settings_from_environment()
            update_status(
                settings.state_path,
                "error",
                error_type=type(error).__name__,
                detail=str(error)[:500],
            )
        except Exception:
            LOGGER.exception("falha ao persistir o estado do atualizador")
        raise


if __name__ == "__main__":
    main()
