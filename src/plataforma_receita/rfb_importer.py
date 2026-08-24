"""Resumable and queue-aware loader for complementary Receita CNPJ data.

Nothing in this module writes to ``rfb_establishments``.  New data is loaded
under a non-current dataset version and only becomes visible through the
``rfb_current_*`` views after every required file has completed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from .rfb_layout import LAYOUTS, Layout
from .rfb_manifest import Manifest, ManifestFile, load_manifest


LOGGER = logging.getLogger("plataforma-receita-full-import")
ADVISORY_LOCK_NAME = "plataforma_receita_aux_import"
REQUIRED_KINDS = {"companies", "establishments", "partners", "simples"}


def read_semicolon_zip(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if not members:
            raise ValueError(f"ZIP vazio: {path.name}")
        for member in members:
            with archive.open(member) as raw:
                lines = (
                    line.decode("latin-1").replace("\x00", "").rstrip("\r\n")
                    for line in raw
                )
                yield from csv.reader(lines, delimiter=";", quotechar='"')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ImportSettings:
    jobs_database: Path | None
    require_jobs_database: bool = True
    max_load_per_cpu: float = 0.45
    stable_idle_seconds: int = 60
    poll_seconds: int = 10
    batch_rows: int = 50_000
    minimum_free_gb: float = 35.0
    maximum_database_gb: float = 140.0
    temp_directory: Path = Path("/tmp")


class QueueAndLoadGate:
    """Pauses the importer whenever the user-facing matcher has work."""

    def __init__(self, settings: ImportSettings):
        self.settings = settings
        self._idle_since: float | None = None
        self._cleared_once = False

    def reasons(self) -> list[str]:
        reasons: list[str] = []
        database = self.settings.jobs_database
        if database is None or not database.exists():
            if self.settings.require_jobs_database:
                reasons.append("banco da fila nao encontrado")
        else:
            try:
                uri = f"file:{database}?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=2) as connection:
                    active = connection.execute(
                        "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
                    ).fetchone()[0]
                if active:
                    reasons.append(f"{active} consulta(s) na fila da plataforma")
            except sqlite3.Error as error:
                reasons.append(f"fila indisponivel: {type(error).__name__}")

        cpu_count = os.cpu_count() or 1
        load_one = os.getloadavg()[0]
        threshold = cpu_count * self.settings.max_load_per_cpu
        if load_one > threshold:
            reasons.append(f"carga do servidor {load_one:.2f} acima de {threshold:.2f}")
        return reasons

    def wait_until_idle(self) -> None:
        last_message: tuple[str, ...] | None = None
        while True:
            reasons = self.reasons()
            if reasons:
                self._idle_since = None
                self._cleared_once = False
                message = tuple(reasons)
                if message != last_message:
                    LOGGER.info("importacao pausada: %s", "; ".join(reasons))
                    last_message = message
            else:
                self._idle_since = self._idle_since or time.monotonic()
                elapsed = time.monotonic() - self._idle_since
                if self._cleared_once or elapsed >= self.settings.stable_idle_seconds:
                    if last_message is not None:
                        LOGGER.info("servidor ocioso; importacao liberada")
                    self._cleared_once = True
                    return
            time.sleep(self.settings.poll_seconds)


class AuxiliaryImporter:
    def __init__(self, connection: Any, settings: ImportSettings):
        self.connection = connection
        self.settings = settings
        self.gate = QueueAndLoadGate(settings)

    def initialize_schema(self) -> None:
        schema = Path(__file__).resolve().parents[2] / "sql" / "auxiliary_schema.sql"
        self.connection.execute(schema.read_text(encoding="utf-8"))
        self.connection.commit()

    def run(self, manifest: Manifest) -> None:
        self._assert_single_importer()
        try:
            self.initialize_schema()
            if self._dataset_is_current(manifest.version):
                LOGGER.info("versao complementar %s ja esta publicada", manifest.version)
                return
            self._register_manifest(manifest)
            for entry in manifest.files:
                if self._file_is_completed(manifest.version, entry.name):
                    LOGGER.info("arquivo ja concluido, ignorando: %s", entry.name)
                    continue
                self.gate.wait_until_idle()
                self._assert_capacity()
                self._process_file(manifest, entry)
            self._publish(manifest)
        except Exception as error:
            self.connection.rollback()
            try:
                self.connection.execute(
                    "UPDATE rfb_aux_datasets SET status='failed',error=%s WHERE version=%s",
                    (type(error).__name__, manifest.version),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
            raise
        finally:
            self.connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
            self.connection.commit()

    def _dataset_is_current(self, version: str) -> bool:
        row = self.connection.execute(
            "SELECT status FROM rfb_aux_datasets WHERE version=%s", (version,)
        ).fetchone()
        return bool(row and row[0] == "current")

    def _assert_single_importer(self) -> None:
        locked = self.connection.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (ADVISORY_LOCK_NAME,)
        ).fetchone()[0]
        if not locked:
            raise RuntimeError("ja existe uma importacao complementar em andamento")

    def _register_manifest(self, manifest: Manifest) -> None:
        self.connection.execute(
            """INSERT INTO rfb_aux_datasets(version,source_url,status,error)
               VALUES (%s,%s,'staging',NULL)
               ON CONFLICT(version) DO UPDATE SET source_url=excluded.source_url,
                 status=CASE WHEN rfb_aux_datasets.status='current' THEN 'current' ELSE 'staging' END,
                 error=NULL""",
            (manifest.version, manifest.source_url),
        )
        for entry in manifest.files:
            self.connection.execute(
                """INSERT INTO rfb_aux_import_files(
                     dataset_version,file_name,kind,source_url,status
                   ) VALUES (%s,%s,%s,%s,'pending')
                   ON CONFLICT(dataset_version,file_name) DO UPDATE SET
                     kind=excluded.kind,source_url=excluded.source_url""",
                (manifest.version, entry.name, entry.kind, entry.url),
            )
        self.connection.commit()

    def _file_is_completed(self, version: str, name: str) -> bool:
        row = self.connection.execute(
            "SELECT status FROM rfb_aux_import_files WHERE dataset_version=%s AND file_name=%s",
            (version, name),
        ).fetchone()
        return bool(row and row[0] == "completed")

    def _process_file(self, manifest: Manifest, entry: ManifestFile) -> None:
        LOGGER.info("preparando %s", entry.name)
        self.connection.execute(
            """UPDATE rfb_aux_import_files SET status='running',started_at=coalesce(started_at,now()),
                 completed_at=NULL,error=NULL
               WHERE dataset_version=%s AND file_name=%s""",
            (manifest.version, entry.name),
        )
        self.connection.commit()
        try:
            with tempfile.TemporaryDirectory(
                prefix="rfb-aux-", dir=self.settings.temp_directory
            ) as directory:
                path = Path(directory) / entry.name
                downloaded = self._download(entry.url, path)
                if entry.size is not None and downloaded != entry.size:
                    raise ValueError(f"tamanho inesperado em {entry.name}")
                if entry.sha256 and sha256_file(path).lower() != entry.sha256.lower():
                    raise ValueError(f"checksum invalido em {entry.name}")
                self.connection.execute(
                    """UPDATE rfb_aux_import_files SET bytes_downloaded=%s
                       WHERE dataset_version=%s AND file_name=%s""",
                    (downloaded, manifest.version, entry.name),
                )
                self.connection.commit()
                self._load_zip(manifest.version, entry, path)
            self.connection.execute(
                """UPDATE rfb_aux_import_files SET status='completed',completed_at=now(),error=NULL
                   WHERE dataset_version=%s AND file_name=%s""",
                (manifest.version, entry.name),
            )
            self.connection.commit()
        except Exception as error:
            self.connection.rollback()
            self.connection.execute(
                """UPDATE rfb_aux_import_files SET status='failed',error=%s
                   WHERE dataset_version=%s AND file_name=%s""",
                (type(error).__name__, manifest.version, entry.name),
            )
            self.connection.commit()
            raise

    def _download(self, url: str, path: Path) -> int:
        request = urllib.request.Request(
            url, headers={"User-Agent": "PlataformaReceitaFullImport/0.1"}
        )
        last_error: Exception | None = None
        for attempt in range(1, 5):
            path.unlink(missing_ok=True)
            downloaded = 0
            try:
                with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded % (64 * 1024 * 1024) < 1024 * 1024:
                            self.gate.wait_until_idle()
                            self._assert_capacity()
                return downloaded
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last_error = error
                path.unlink(missing_ok=True)
                if attempt < 4:
                    time.sleep(min(30, 2**attempt))
        raise last_error or RuntimeError("download sem resultado")

    def _load_zip(self, version: str, entry: ManifestFile, path: Path) -> None:
        layout = LAYOUTS[entry.kind]
        progress = self.connection.execute(
            """SELECT source_rows_processed FROM rfb_aux_import_files
               WHERE dataset_version=%s AND file_name=%s""",
            (version, entry.name),
        ).fetchone()[0]
        temporary = f"aux_batch_{layout.table}_{os.getpid()}"
        self.connection.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {temporary} "
            f"(LIKE {layout.table} INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS"
        )
        self.connection.commit()

        batch: list[tuple] = []
        source_index = 0
        for source_index, row in enumerate(read_semicolon_zip(path), start=1):
            if source_index <= progress:
                continue
            parsed = layout.parser(row, version)
            if parsed is not None:
                batch.append(parsed)
            if len(batch) >= self.settings.batch_rows:
                self._flush_batch(version, entry, layout, temporary, batch, source_index)
                batch = []
        if batch or source_index > progress:
            self._flush_batch(version, entry, layout, temporary, batch, source_index)

    def _flush_batch(
        self,
        version: str,
        entry: ManifestFile,
        layout: Layout,
        temporary: str,
        batch: Iterable[tuple],
        source_index: int,
    ) -> None:
        rows = list(batch)
        self.gate.wait_until_idle()
        self._assert_capacity()
        self.connection.execute(f"TRUNCATE {temporary}")
        columns = ",".join(layout.columns)
        if rows:
            with self.connection.cursor().copy(
                f"COPY {temporary} ({columns}) FROM STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(row)
            conflicts = ",".join(layout.conflict_columns)
            self.connection.execute(
                f"INSERT INTO {layout.table} ({columns}) "
                f"SELECT {columns} FROM {temporary} "
                f"ON CONFLICT ({conflicts}) DO NOTHING"
            )
        self.connection.execute(
            """UPDATE rfb_aux_import_files SET source_rows_processed=%s,
                 rows_loaded=rows_loaded+%s
               WHERE dataset_version=%s AND file_name=%s""",
            (source_index, len(rows), version, entry.name),
        )
        self.connection.commit()

    def _assert_capacity(self) -> None:
        free_gb = shutil.disk_usage(self.settings.temp_directory).free / 1024**3
        if free_gb < self.settings.minimum_free_gb:
            raise RuntimeError("espaco livre abaixo da margem de seguranca")
        database_gb = self.connection.execute(
            "SELECT pg_database_size(current_database())"
        ).fetchone()[0] / 1024**3
        if database_gb > self.settings.maximum_database_gb:
            raise RuntimeError("banco acima do limite operacional configurado")

    def _publish(self, manifest: Manifest) -> None:
        statuses = self.connection.execute(
            """SELECT kind,bool_and(status='completed')
               FROM rfb_aux_import_files WHERE dataset_version=%s GROUP BY kind""",
            (manifest.version,),
        ).fetchall()
        completed = {kind for kind, ready in statuses if ready}
        missing = REQUIRED_KINDS - completed
        if missing:
            raise RuntimeError(f"nao e possivel publicar; faltam: {', '.join(sorted(missing))}")

        self.connection.execute(
            "UPDATE rfb_aux_datasets SET status='ready' WHERE status='current' AND version<>%s",
            (manifest.version,),
        )
        self.connection.execute(
            """UPDATE rfb_aux_datasets SET status='current',published_at=now(),completed_at=now(),
                 error=NULL,metadata=jsonb_build_object(
                   'files',(SELECT count(*) FROM rfb_aux_import_files WHERE dataset_version=%s),
                   'rows',(SELECT coalesce(sum(rows_loaded),0) FROM rfb_aux_import_files WHERE dataset_version=%s)
                 ) WHERE version=%s""",
            (manifest.version, manifest.version, manifest.version),
        )
        self.connection.commit()
        LOGGER.info("versao complementar %s publicada", manifest.version)


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa os dados complementares do CNPJ")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--jobs-db", type=Path)
    parser.add_argument("--allow-missing-jobs-db", action="store_true")
    parser.add_argument("--temp-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--batch-rows", type=int, default=50_000)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.45)
    parser.add_argument("--minimum-free-gb", type=float, default=35.0)
    parser.add_argument("--maximum-database-gb", type=float, default=140.0)
    args = parser.parse_args()

    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("configure POSTGRES_DSN sem gravar a credencial no repositorio")
    try:
        import psycopg
    except ModuleNotFoundError as error:
        raise SystemExit("instale as dependencias do projeto") from error

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    manifest = load_manifest(args.manifest)
    settings = ImportSettings(
        jobs_database=args.jobs_db,
        require_jobs_database=not args.allow_missing_jobs_db,
        max_load_per_cpu=args.max_load_per_cpu,
        batch_rows=args.batch_rows,
        minimum_free_gb=args.minimum_free_gb,
        maximum_database_gb=args.maximum_database_gb,
        temp_directory=args.temp_dir,
    )
    with psycopg.connect(dsn) as connection:
        AuxiliaryImporter(connection, settings).run(manifest)


if __name__ == "__main__":
    main()
