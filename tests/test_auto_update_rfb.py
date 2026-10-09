import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plataforma_receita.rfb_manifest import Manifest, ManifestFile
from scripts.auto_update_rfb import (
    ManifestNotReady,
    active_jobs,
    manifest_fingerprint,
    month_after,
    stable_since,
    sync_legacy_compatibility,
    validate_release_manifest,
)


def complete_manifest(version: str = "2026-10", *, shards: int = 10) -> Manifest:
    files = []
    for kind, prefix in (
        ("companies", "Empresas"),
        ("establishments", "Estabelecimentos"),
        ("partners", "Socios"),
    ):
        files.extend(
            ManifestFile(
                kind=kind,
                name=f"{prefix}{index}.zip",
                url=f"https://example.test/{version}/{prefix}{index}.zip",
                size=100 + index,
            )
            for index in range(shards)
        )
    files.append(ManifestFile(
        kind="simples", name="Simples.zip",
        url=f"https://example.test/{version}/Simples.zip", size=200,
    ))
    for kind, name in (
        ("reference_cnaes", "Cnaes.zip"),
        ("reference_countries", "Paises.zip"),
        ("reference_legal_natures", "Naturezas.zip"),
        ("reference_municipalities", "Municipios.zip"),
        ("reference_qualifications", "Qualificacoes.zip"),
        ("reference_status_reasons", "Motivos.zip"),
    ):
        files.append(ManifestFile(
            kind=kind, name=name, url=f"https://example.test/{version}/{name}", size=10,
        ))
    return Manifest(
        version=version,
        source_url=f"https://example.test/{version}/",
        files=tuple(files),
    )


class AutoUpdateTests(unittest.TestCase):
    def test_month_after_handles_year_boundary(self):
        self.assertEqual(month_after("2026-09"), "2026-10")
        self.assertEqual(month_after("2026-12"), "2027-01")
        with self.assertRaises(ValueError):
            month_after("2026-13")

    def test_release_requires_complete_equal_contiguous_shards_and_references(self):
        manifest = complete_manifest()
        summary = validate_release_manifest(manifest)
        self.assertEqual(summary["files"], 37)
        self.assertEqual(summary["shards"], 10)

        incomplete = Manifest(
            version=manifest.version,
            source_url=manifest.source_url,
            files=tuple(
                item for item in manifest.files
                if not (item.kind == "partners" and item.name == "Socios9.zip")
            ),
        )
        with self.assertRaisesRegex(ManifestNotReady, "partners"):
            validate_release_manifest(incomplete)

    def test_manifest_fingerprint_includes_official_sizes(self):
        original = complete_manifest()
        changed_files = list(original.files)
        changed = changed_files[0]
        changed_files[0] = ManifestFile(
            kind=changed.kind, name=changed.name, url=changed.url, size=changed.size + 1,
        )
        updated = Manifest(original.version, original.source_url, tuple(changed_files))
        self.assertNotEqual(manifest_fingerprint(original), manifest_fingerprint(updated))

    def test_stability_window_resets_when_fingerprint_changes(self):
        now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        previous = {
            "target_version": "2026-10",
            "manifest_fingerprint": "same",
            "manifest_first_seen_at": (now - timedelta(hours=19)).isoformat(),
        }
        stable, first_seen = stable_since(
            previous, version="2026-10", fingerprint="same", now=now, stability_hours=18,
        )
        self.assertTrue(stable)
        self.assertEqual(first_seen, now - timedelta(hours=19))
        stable, first_seen = stable_since(
            previous, version="2026-10", fingerprint="changed", now=now, stability_hours=18,
        )
        self.assertFalse(stable)
        self.assertEqual(first_seen, now)

    def test_active_jobs_reads_each_queue_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.sqlite"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE jobs(status text)")
                connection.executemany(
                    "INSERT INTO jobs(status) VALUES (?)",
                    [("queued",), ("running",), ("completed",)],
                )
            self.assertEqual(active_jobs((path,)), {str(path): 2})

    def test_legacy_sync_batches_files_through_a_cursor(self):
        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class Cursor:
            def __init__(self):
                self.batch = None

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def executemany(self, statement, values):
                self.batch = (statement, values)

        class Connection:
            def __init__(self):
                self.statements = []
                self.test_cursor = Cursor()
                self.committed = False

            def execute(self, statement, parameters=None):
                self.statements.append((statement, parameters))
                if "SELECT relname FROM pg_class" in statement:
                    return Result([
                        {"relname": "rfb_aux_datasets"},
                        {"relname": "rfb_aux_import_files"},
                        {"relname": "rfb_aux_reference"},
                    ])
                if "RETURNING 1" in statement:
                    return Result([{"?column?": 1}] * 6)
                return Result([])

            def cursor(self):
                return self.test_cursor

            def commit(self):
                self.committed = True

        connection = Connection()
        manifest = complete_manifest()
        result = sync_legacy_compatibility(connection, manifest)
        self.assertEqual(result, {"files": 37, "references": 6})
        self.assertEqual(len(connection.test_cursor.batch[1]), 37)
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()
