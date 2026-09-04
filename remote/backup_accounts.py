"""Run on VPS before organization deployment. No credentials or personal data in output."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2


def main():
    root = Path("/srv/plataforma-receita")
    data = root / "data"
    with sqlite3.connect(f"file:{data / 'jobs.sqlite'}?mode=ro", uri=True) as jobs:
        active = jobs.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
    if active:
        raise SystemExit("Há consultas ativas. Publicação adiada; nenhum backup/substituição foi feito.")
    backup = root / "backups" / ("organizations-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    for name in ("auth.sqlite", "jobs.sqlite"):
        target = backup / name
        with sqlite3.connect(f"file:{data / name}?mode=ro", uri=True) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
            assert destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        os.chmod(target, 0o600)
    copy2("/opt/plataforma-receita/compose.vps.yml", backup / "compose.vps.yml")
    print(json.dumps({"backup": str(backup), "integrity": "ok", "active_jobs": active}))


if __name__ == "__main__":
    main()
