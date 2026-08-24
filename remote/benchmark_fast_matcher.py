import json
from collections import Counter
from pathlib import Path

from app.config import get_settings
from service.repository import Repository


SOURCE = Path("/tmp/plataforma-receita-pilot-input.json")
OUTPUT = Path("/tmp/plataforma-receita-fast-benchmark.json")


def main():
    source_rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    items = [
        {
            "local_id": str(row["row_number"]),
            "name": row["company_name"],
            "address": row.get("street") or row.get("address"),
            "municipality": row.get("city"),
            "uf": row.get("state") or "SP",
            "postal_code": row.get("postal_code"),
            "website": row.get("website"),
            "cnpj": None,
            "site_cnpjs": [],
            "site_names": [],
        }
        for row in source_rows
    ]
    repository = Repository(get_settings().postgres_dsn, database_workers=8, statement_timeout_ms=1800)
    repository.open()
    try:
        timings = {}
        results = []
        version = None
        duration_ms = 0
        for size in (1, 10, len(items)):
            batch_results, version, batch_duration_ms = repository.match_batch(items[:size], active_only=True)
            timings[str(size)] = batch_duration_ms
            if size == len(items):
                results = batch_results
                duration_ms = batch_duration_ms
    finally:
        repository.close()
    summary = Counter(result["status"] for result in results)
    OUTPUT.write_text(json.dumps({"results": results, "version": version, "duration_ms": duration_ms, "timings": timings}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"timings": timings, "version": version, "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
