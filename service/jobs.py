import csv
import io
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, database_path: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              filename TEXT NOT NULL,
              status TEXT NOT NULL,
              total INTEGER NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0,
              confirmed INTEGER NOT NULL DEFAULT 0,
              review INTEGER NOT NULL DEFAULT 0,
              not_found INTEGER NOT NULL DEFAULT 0,
              failed INTEGER NOT NULL DEFAULT 0,
              active_only INTEGER NOT NULL,
              check_website INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS job_items (
              job_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              input_json TEXT NOT NULL,
              result_json TEXT,
              status TEXT NOT NULL DEFAULT 'queued',
              error TEXT,
              PRIMARY KEY(job_id, position),
              FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_job_items_pending ON job_items(job_id, status, position);
        """)
        # A restart resumes from the first item that has no stored result.
        self._connection.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        job["active_only"] = bool(job["active_only"])
        job["check_website"] = bool(job["check_website"])
        job["progress_percent"] = round(100 * job["processed"] / job["total"], 1) if job["total"] else 0
        return job

    def create_job(
        self,
        filename: str,
        items: list[dict[str, Any]],
        *,
        active_only: bool,
        check_website: bool,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        created_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO jobs(
                     id,filename,status,total,active_only,check_website,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (job_id, filename, "queued", len(items), int(active_only), int(check_website), created_at),
            )
            self._connection.executemany(
                "INSERT INTO job_items(job_id,position,input_json) VALUES(?,?,?)",
                [
                    (job_id, position, json.dumps(item, ensure_ascii=False))
                    for position, item in enumerate(items)
                ],
            )
        return self.get_job(job_id)

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT * FROM jobs
                   WHERE status IN ('running','queued')
                   ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, created_at
                   LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            if row["status"] == "queued":
                self._connection.execute(
                    "UPDATE jobs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?",
                    (utc_now(), row["id"]),
                )
            updated = self._connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return self._job(updated)

    def next_item(self, job_id: str) -> tuple[int, dict[str, Any]] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT position,input_json FROM job_items
                   WHERE job_id=? AND status='queued'
                   ORDER BY position LIMIT 1""",
                (job_id,),
            ).fetchone()
        return (row["position"], json.loads(row["input_json"])) if row else None

    def complete_item(self, job_id: str, position: int, response: dict[str, Any]) -> None:
        result_status = response["results"][0]["status"]
        column = {
            "confirmado": "confirmed",
            "revisao": "review",
            "nao_encontrado": "not_found",
        }.get(result_status, "not_found")
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE job_items SET status='completed',result_json=?
                   WHERE job_id=? AND position=? AND status='queued'""",
                (json.dumps(response, ensure_ascii=False), job_id, position),
            )
            self._connection.execute(
                f"UPDATE jobs SET processed=processed+1,{column}={column}+1 WHERE id=?",
                (job_id,),
            )

    def fail_item(self, job_id: str, position: int, error: Exception) -> None:
        safe_error = type(error).__name__
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE job_items SET status='failed',error=?
                   WHERE job_id=? AND position=? AND status='queued'""",
                (safe_error, job_id, position),
            )
            self._connection.execute(
                "UPDATE jobs SET processed=processed+1,failed=failed+1 WHERE id=?",
                (job_id,),
            )

    def finish_if_complete(self, job_id: str) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT total,processed,failed FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row or row["processed"] < row["total"]:
                return False
            status = "completed_with_errors" if row["failed"] else "completed"
            self._connection.execute(
                "UPDATE jobs SET status=?,finished_at=COALESCE(finished_at,?) WHERE id=?",
                (status, utc_now(), job_id),
            )
            return True

    def export_csv(self, job_id: str) -> bytes | None:
        job = self.get_job(job_id)
        if not job:
            return None
        with self._lock:
            rows = self._connection.execute(
                """SELECT position,input_json,result_json,status,error
                   FROM job_items WHERE job_id=? ORDER BY position""",
                (job_id,),
            ).fetchall()
        parsed = []
        source_headers: list[str] = []
        for row in rows:
            item = json.loads(row["input_json"])
            source = item.get("source") or {}
            for header in source:
                if header not in source_headers:
                    source_headers.append(header)
            response = json.loads(row["result_json"]) if row["result_json"] else None
            parsed.append((item, source, response, row["status"], row["error"]))

        extra_headers = [
            "Matcher Status", "CNPJ Receita", "Razão Social Receita", "Nome Fantasia Receita",
            "Score", "Confiança", "Versão Receita", "Erro",
        ]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([*source_headers, *extra_headers])
        for item, source, response, item_status, error in parsed:
            result = response["results"][0] if response else {}
            selected = result.get("selected") or {}
            writer.writerow([
                *[source.get(header, "") for header in source_headers],
                result.get("status", item_status),
                selected.get("cnpj", ""),
                selected.get("legal_name", ""),
                selected.get("trade_name", ""),
                selected.get("score", ""),
                result.get("confidence", ""),
                response.get("dataset_version", "") if response else "",
                error or "",
            ])
        return ("\ufeff" + output.getvalue()).encode("utf-8")


class JobRunner:
    def __init__(self, store: JobStore, match_one: Callable[..., dict[str, Any]]):
        self.store = store
        self.match_one = match_one
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cnpj-job-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=15)

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next_job()
            if not job:
                self._wake.wait(timeout=2)
                self._wake.clear()
                continue
            pending = self.store.next_item(job["id"])
            if not pending:
                self.store.finish_if_complete(job["id"])
                continue
            position, item = pending
            try:
                response = self.match_one(
                    [item],
                    active_only=job["active_only"],
                    check_website=job["check_website"],
                )
                self.store.complete_item(job["id"], position, response)
            except Exception as error:
                self.store.fail_item(job["id"], position, error)
            self.store.finish_if_complete(job["id"])
