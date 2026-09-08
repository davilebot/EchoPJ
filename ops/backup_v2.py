#!/usr/bin/env python3
"""Create and verify consistent, atomic SQLite backups for EchoPJs SaaS v2."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


CRITICAL_DATABASES = ("auth.sqlite", "saas.sqlite", "jobs.sqlite")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()


def backup_database(source: Path, destination: Path) -> dict:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
        destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal_mode = destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(journal_mode).casefold() != "delete":
            raise RuntimeError(f"could not normalize journal mode for {source.name}")
        result = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check failed for {source.name}: {result}")
    finally:
        destination_connection.close()
        source_connection.close()
    for suffix in ("-wal", "-shm"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)
    destination.chmod(0o600)
    return {"name": source.name, "size": destination.stat().st_size, "sha256": sha256(destination)}


def verify_backup(backup_dir: Path) -> dict:
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_names = [item.get("name") for item in manifest.get("files", [])]
    if manifest_names != list(CRITICAL_DATABASES):
        raise RuntimeError("manifest does not contain the expected critical databases")
    verified = []
    for item in manifest["files"]:
        path = backup_dir / item["name"]
        if not path.is_file():
            raise RuntimeError(f"missing backup file: {item['name']}")
        if sha256(path) != item["sha256"]:
            raise RuntimeError(f"checksum mismatch: {item['name']}")
        result = integrity(path)
        if result != "ok":
            raise RuntimeError(f"integrity_check failed for {item['name']}: {result}")
        verified.append(item["name"])
    return {"status": "ok", "backup": str(backup_dir), "verified": verified}


def create_backup(source_dir: Path, destination_dir: Path, *, retention_count: int = 56, now: datetime | None = None) -> dict:
    if retention_count < 2:
        raise ValueError("retention_count must be at least 2")
    missing = [name for name in CRITICAL_DATABASES if not (source_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"missing critical databases: {', '.join(missing)}")
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination_dir.chmod(0o700)
    lock_path = destination_dir / ".backup.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final_dir = destination_dir / f"backup-{timestamp}"
        partial_dir = destination_dir / f".backup-{timestamp}.partial"
        if final_dir.exists() or partial_dir.exists():
            raise RuntimeError(f"backup timestamp already exists: {timestamp}")
        partial_dir.mkdir(mode=0o700)
        try:
            files = [backup_database(source_dir / name, partial_dir / name) for name in CRITICAL_DATABASES]
            manifest = {
                "format": 1,
                "created_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "source": str(source_dir),
                "files": files,
            }
            manifest_path = partial_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest_path.chmod(0o600)
            verify_backup(partial_dir)
            os.replace(partial_dir, final_dir)
        except Exception:
            shutil.rmtree(partial_dir, ignore_errors=True)
            raise

        backups = sorted(
            (path for path in destination_dir.glob("backup-*") if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        for expired in backups[retention_count:]:
            shutil.rmtree(expired)
        result = verify_backup(final_dir)
        result["retained"] = min(len(backups), retention_count)
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("/srv/echopjs-saas-v2/data"))
    parser.add_argument("--destination-dir", type=Path, default=Path("/srv/echopjs-saas-v2/backups"))
    parser.add_argument("--retention-count", type=int, default=56)
    parser.add_argument("--verify", type=Path, help="Verify one existing backup and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = verify_backup(args.verify) if args.verify else create_backup(
        args.source_dir,
        args.destination_dir,
        retention_count=args.retention_count,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
