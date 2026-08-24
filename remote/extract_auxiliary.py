import csv
import json
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


BASE_URL = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/2026-08-09/"
TARGETS_PATH = Path("/tmp/plataforma-receita-auxiliary-targets.json")
OUTPUT_PATH = Path("/tmp/plataforma-receita-pilot-auxiliary.json")
STATE_PATH = Path("/tmp/plataforma-receita-auxiliary-state.json")
USER_AGENT = "PlataformaReceitaPilot/0.1"

PARTNER_TYPES = {"1": "PESSOA JURIDICA", "2": "PESSOA FISICA", "3": "ESTRANGEIRO"}
AGE_RANGES = {
    "1": "0 A 12 ANOS", "2": "13 A 20 ANOS", "3": "21 A 30 ANOS",
    "4": "31 A 40 ANOS", "5": "41 A 50 ANOS", "6": "51 A 60 ANOS",
    "7": "61 A 70 ANOS", "8": "71 A 80 ANOS", "9": "MAIOR DE 80 ANOS",
}
SIZE_LABELS = {
    "00": "NAO INFORMADO", "01": "MICRO EMPRESA",
    "03": "EMPRESA DE PEQUENO PORTE", "05": "DEMAIS",
}


def date_or_none(value):
    value = (value or "").strip()
    if len(value) == 8 and value != "00000000":
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def download(filename, destination):
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(BASE_URL + filename, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
            return
        except Exception as error:
            last_error = error
            if destination.exists():
                destination.unlink()
            time.sleep(2 * (attempt + 1))
    raise last_error


def rows_from_zip(path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return
        with archive.open(names[0]) as raw:
            text = (line.decode("latin-1") for line in raw)
            yield from csv.reader(text, delimiter=";")


def load_dimension(filename):
    with tempfile.TemporaryDirectory(prefix="rfb-dim-") as directory:
        path = Path(directory) / filename
        download(filename, path)
        return {row[0].strip(): row[1].strip() for row in rows_from_zip(path) if len(row) >= 2}


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"processed": [], "companies": {}, "simples": {}, "partners": []}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def process_file(filename, kind, target_roots, state):
    if filename in state["processed"]:
        return
    with tempfile.TemporaryDirectory(prefix="rfb-pilot-") as directory:
        path = Path(directory) / filename
        download(filename, path)
        matched = 0
        for row in rows_from_zip(path):
            if not row or row[0].strip() not in target_roots:
                continue
            root = row[0].strip()
            if kind == "companies" and len(row) >= 7:
                state["companies"][root] = {
                    "cnpj_root": root,
                    "legal_name": row[1].strip() or None,
                    "legal_nature_code": row[2].strip() or None,
                    "responsible_qualification_code": row[3].strip() or None,
                    "company_size_code": row[4].strip() or None,
                    "company_size": SIZE_LABELS.get(row[4].strip(), "NAO INFORMADO"),
                    "share_capital": row[5].strip() or None,
                    "federative_entity": row[6].strip() or None,
                }
            elif kind == "simples" and len(row) >= 7:
                state["simples"][root] = {
                    "cnpj_root": root,
                    "simples_option": row[1].strip() == "S",
                    "simples_option_raw": row[1].strip() or None,
                    "simples_started_at": date_or_none(row[2]),
                    "simples_ended_at": date_or_none(row[3]),
                    "mei_option": row[4].strip() == "S",
                    "mei_option_raw": row[4].strip() or None,
                    "mei_started_at": date_or_none(row[5]),
                    "mei_ended_at": date_or_none(row[6]),
                }
            elif kind == "partners" and len(row) >= 11:
                state["partners"].append({
                    "cnpj_root": root,
                    "partner_type_code": row[1].strip() or None,
                    "partner_type": PARTNER_TYPES.get(row[1].strip(), "NAO INFORMADO"),
                    "partner_name": row[2].strip() or None,
                    "partner_document": row[3].strip() or None,
                    "qualification_code": row[4].strip() or None,
                    "joined_at": date_or_none(row[5]),
                    "country_code": row[6].strip() or None,
                    "legal_representative_document": row[7].strip() or None,
                    "legal_representative_name": row[8].strip() or None,
                    "legal_representative_qualification_code": row[9].strip() or None,
                    "age_range_code": row[10].strip() or None,
                    "age_range": AGE_RANGES.get(row[10].strip(), "NAO INFORMADO"),
                })
            matched += 1
        state["processed"].append(filename)
        save_state(state)
        print(json.dumps({"file": filename, "matched_rows": matched}), flush=True)


def main():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    target_roots = {item["cnpj_root"] for item in targets}
    state = load_state()
    qualifications = load_dimension("Qualificacoes.zip")
    countries = load_dimension("Paises.zip")
    legal_natures = load_dimension("Naturezas.zip")
    cnaes = load_dimension("Cnaes.zip")

    for index in range(10):
        process_file(f"Empresas{index}.zip", "companies", target_roots, state)
    process_file("Simples.zip", "simples", target_roots, state)
    for index in range(10):
        process_file(f"Socios{index}.zip", "partners", target_roots, state)

    for company in state["companies"].values():
        company["legal_nature"] = legal_natures.get(company.get("legal_nature_code"))
        company["responsible_qualification"] = qualifications.get(company.get("responsible_qualification_code"))
    for partner in state["partners"]:
        partner["qualification"] = qualifications.get(partner.get("qualification_code"))
        partner["country"] = countries.get(partner.get("country_code"))
        partner["legal_representative_qualification"] = qualifications.get(partner.get("legal_representative_qualification_code"))

    payload = {
        "dataset_version": "2026-08",
        "source": "Receita Federal - Dados Abertos do CNPJ",
        "companies": state["companies"],
        "simples": state["simples"],
        "partners": state["partners"],
        "cnae_descriptions": cnaes,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "targets": len(target_roots),
        "companies": len(state["companies"]),
        "simples": len(state["simples"]),
        "partners": len(state["partners"]),
    }), flush=True)


if __name__ == "__main__":
    main()
