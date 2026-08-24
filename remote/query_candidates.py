import json
import re
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings


INPUT = Path("/tmp/plataforma-receita-pilot-input.json")
OUTPUT = Path("/tmp/plataforma-receita-pilot-candidates.json")


def normalize(value):
    raw = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in raw if not unicodedata.combining(char)).upper()
    return re.sub(r"[^A-Z0-9]+", " ", plain).strip()


def domain_name(value):
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.casefold().removeprefix("www.")
    first = host.split(".")[0]
    return normalize(first)


def serializable(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def candidate(row):
    address = " ".join(filter(None, [row.get("street_type"), row.get("street"), row.get("street_number"), row.get("address_extra"), row.get("district")]))
    fields = {
        "cnpj": row["cnpj"],
        "cnpj_root": row["cnpj_root"],
        "legal_name": row.get("legal_name"),
        "trade_name": row.get("trade_name"),
        "registration_status": row.get("registration_status"),
        "registration_status_date": row.get("registration_status_date"),
        "opened_at": row.get("opened_at"),
        "company_size": row.get("company_size"),
        "share_capital": row.get("share_capital"),
        "primary_cnae": row.get("primary_cnae"),
        "secondary_cnaes": row.get("secondary_cnaes") or [],
        "municipality": row.get("municipality"),
        "uf": row.get("uf"),
        "postal_code": row.get("postal_code"),
        "address": address,
        "dataset_version": row.get("dataset_version"),
    }
    return {key: serializable(value) for key, value in fields.items()}


def add_rows(target, rows):
    for row in rows:
        target[row["cnpj"]] = candidate(row)


def main():
    items = json.loads(INPUT.read_text(encoding="utf-8"))
    pool = ConnectionPool(get_settings().postgres_dsn, min_size=1, max_size=2, kwargs={"row_factory": dict_row})
    results = []
    with pool.connection() as connection:
        connection.execute("SET statement_timeout = '4s'")
        version_row = connection.execute("SELECT version FROM dataset_versions WHERE is_current AND status='ready' LIMIT 1").fetchone()
        version = version_row["version"] if version_row else None
        for item_index, item in enumerate(items, start=1):
            found = {}
            for cnpj in item.get("site_cnpjs", []):
                rows = connection.execute("SELECT * FROM rfb_establishments WHERE cnpj=%s AND is_active LIMIT 1", (cnpj,)).fetchall()
                add_rows(found, rows)

            cep = re.sub(r"\D", "", item.get("postal_code") or "")
            city = normalize(item.get("city"))
            uf = item.get("state") or "SP"
            names = []
            for value in [item.get("company_name"), *item.get("site_names", []), domain_name(item.get("website"))]:
                value = normalize(value)
                if len(value) >= 3 and value not in names:
                    names.append(value)
            names = names[:3]
            main_name = names[0] if names else ""
            direct_found = bool(found)
            best_cep_name_similarity = 0.0

            if len(cep) == 8:
                rows = connection.execute("""
                    SELECT *, greatest(similarity(normalized_legal_name,%s), similarity(normalized_trade_name,%s)) AS query_similarity
                    FROM rfb_establishments
                    WHERE uf=%s AND postal_code=%s AND is_active
                    ORDER BY query_similarity DESC
                    LIMIT 80
                """, (main_name, main_name, uf, cep)).fetchall()
                best_cep_name_similarity = max((float(row.get("query_similarity") or 0) for row in rows), default=0.0)
                add_rows(found, rows)

            need_name_search = not direct_found and best_cep_name_similarity < 0.32
            if names and need_name_search:
                for name in names[:1]:
                    for column in ("normalized_legal_name", "normalized_trade_name"):
                        if city:
                            location_sql = "municipality=%s"
                            values = (uf, city, name, name, 20)
                        else:
                            location_sql = "TRUE"
                            values = (uf, name, name, 20)
                        sql = f"""
                            SELECT *, similarity({column},%s) AS query_similarity
                            FROM rfb_establishments
                            WHERE uf=%s AND {location_sql} AND is_active AND {column} %% %s
                            ORDER BY query_similarity DESC
                            LIMIT %s
                        """
                        if city:
                            values = (name, uf, city, name, 20)
                        else:
                            values = (name, uf, name, 20)
                        try:
                            rows = connection.execute(sql, values).fetchall()
                            add_rows(found, rows)
                        except Exception:
                            connection.rollback()
                            connection.execute("SET statement_timeout = '4s'")
            if item_index % 20 == 0:
                print(f"consultadas={item_index}", file=sys.stderr, flush=True)

            results.append({"row_number": item["row_number"], "dataset_version": version, "candidates": list(found.values())})
    pool.close()
    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(results), "candidate_rows": sum(len(item["candidates"]) for item in results), "dataset_version": results[0]["dataset_version"] if results else None}))


if __name__ == "__main__":
    main()
