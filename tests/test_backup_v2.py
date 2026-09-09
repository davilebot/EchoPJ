import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.backup_v2 import CRITICAL_DATABASES, create_backup, verify_backup, write_status


class BackupV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / "data"
        self.destination = Path(self.tmp.name) / "backups"
        self.source.mkdir()
        for name in CRITICAL_DATABASES:
            connection = sqlite3.connect(self.source / name)
            connection.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY,value TEXT)")
            connection.execute("INSERT INTO facts(value) VALUES(?)", (name,))
            connection.commit()
            connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_is_consistent_atomic_and_verifiable(self):
        moment = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        result = create_backup(self.source, self.destination, now=moment)
        backup = Path(result["backup"])
        self.assertEqual(result["verified"], list(CRITICAL_DATABASES))
        self.assertFalse(any(path.name.endswith(".partial") for path in self.destination.iterdir()))
        self.assertFalse(any(path.name.endswith(("-wal", "-shm")) for path in backup.iterdir()))
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual([item["name"] for item in manifest["files"]], list(CRITICAL_DATABASES))
        self.assertEqual(verify_backup(backup)["status"], "ok")
        with sqlite3.connect(backup / "auth.sqlite") as connection:
            self.assertEqual(connection.execute("SELECT value FROM facts").fetchone()[0], "auth.sqlite")

    def test_retention_runs_only_after_a_valid_backup(self):
        start = datetime(2026, 9, 8, 0, tzinfo=timezone.utc)
        for offset in range(4):
            create_backup(self.source, self.destination, retention_count=2, now=start + timedelta(hours=offset))
        backups = sorted(path.name for path in self.destination.glob("backup-*"))
        self.assertEqual(backups, ["backup-20260908T020000Z", "backup-20260908T030000Z"])

    def test_missing_database_and_corruption_fail_closed(self):
        (self.source / "jobs.sqlite").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing critical databases"):
            create_backup(self.source, self.destination)
        connection = sqlite3.connect(self.source / "jobs.sqlite")
        connection.execute("CREATE TABLE restored(id INTEGER)")
        connection.close()
        backup = Path(create_backup(self.source, self.destination)["backup"])
        (backup / "auth.sqlite").write_bytes(b"corrupted")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            verify_backup(backup)

    def test_status_file_is_private_atomic_and_readable(self):
        status = self.source / "backup-status.json"
        write_status(status, {"status": "ok", "checked_at": "2026-09-09T00:00:00+00:00", "retained": 4})
        self.assertEqual(json.loads(status.read_text())["retained"], 4)
        self.assertEqual(status.stat().st_mode & 0o777, 0o600)
        self.assertFalse(status.with_name(".backup-status.json.partial").exists())


if __name__ == "__main__":
    unittest.main()
