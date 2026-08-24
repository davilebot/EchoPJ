import csv
import json
from collections import Counter
from pathlib import Path


SOURCE = Path("/Users/davibotelho/Downloads/apollo-accounts-export (89).csv")
INPUTS = Path("work/pilot_input.json")
MATCHES = Path("work/pilot_matches.json")
AUXILIARY = Path("work/pilot_auxiliary.json")
OUTPUT = Path("work/workbook_data.json")


def format_cnpj(value):
    value = "".join(char for char in (value or "") if char.isdigit())
    if len(value) != 14:
        return value or None
    return f"{value[:2]}.{value[2:5]}.{value[5:8]}/{value[8:12]}-{value[12:]}"


def yes_no(value, missing="NÃO DISPONÍVEL"):
    if value is True:
        return "SIM"
    if value is False:
        return "NÃO"
    return missing


with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
    reader = csv.DictReader(stream)
    original_headers = reader.fieldnames or []
    original_rows = []
    for index, row in enumerate(reader, start=1):
        if index > 200:
            break
        original_rows.append(row)

inputs = {item["row_number"]: item for item in json.loads(INPUTS.read_text(encoding="utf-8"))}
matches = json.loads(MATCHES.read_text(encoding="utf-8"))
auxiliary = json.loads(AUXILIARY.read_text(encoding="utf-8"))
companies = auxiliary["companies"]
simples = auxiliary["simples"]
cnae_descriptions = auxiliary["cnae_descriptions"]

status_labels = {
    "confirmado": "Confirmado",
    "revisao": "Revisão manual",
    "nao_encontrado": "Não encontrado",
}

enriched = []
audit = []
review = []
confirmed_roots = set()
for original, match in zip(original_rows, matches):
    item = inputs[match["row_number"]]
    selected = match.get("selected") or {}
    confirmed = match["status"] == "confirmado" and bool(selected)
    root = selected.get("cnpj_root") if confirmed else None
    company = companies.get(root, {}) if root else {}
    simple = simples.get(root) if root else None
    if root:
        confirmed_roots.add(root)
    signals = selected.get("signals", {})
    primary_cnae = selected.get("primary_cnae") if confirmed else None
    secondary = selected.get("secondary_cnaes", []) if confirmed else []
    row = dict(original)
    row.update({
        "Piloto - Linha original": match["row_number"],
        "Match - Status": status_labels[match["status"]],
        "Match - Confiança (0-5)": match["confidence"],
        "Match - Score": selected.get("score"),
        "Match - Margem para 2º candidato": selected.get("margem_para_segundo"),
        "Match - CNPJ extraído do site": yes_no(signals.get("cnpj_extraido_do_site"), "NÃO"),
        "Match - Similaridade do nome": signals.get("nome"),
        "Match - CEP coincide": yes_no(signals.get("cep"), "NÃO"),
        "Match - Município coincide": yes_no(signals.get("municipio"), "NÃO"),
        "Match - Similaridade do logradouro": signals.get("logradouro"),
        "Match - Número coincide": yes_no(signals.get("numero"), "NÃO"),
        "Site - Situação da consulta": item.get("site_status"),
        "Receita - CNPJ": format_cnpj(selected.get("cnpj")) if confirmed else None,
        "Receita - Razão social": selected.get("legal_name") if confirmed else None,
        "Receita - Nome fantasia": selected.get("trade_name") if confirmed else None,
        "Receita - Situação cadastral": selected.get("registration_status") if confirmed else None,
        "Receita - Data da situação": selected.get("registration_status_date") if confirmed else None,
        "Receita - Data de abertura": selected.get("opened_at") if confirmed else None,
        "Receita - Natureza jurídica (código)": company.get("legal_nature_code"),
        "Receita - Natureza jurídica": company.get("legal_nature"),
        "Receita - Porte (código)": company.get("company_size_code") if confirmed else None,
        "Receita - Porte": company.get("company_size") if confirmed else None,
        "Receita - Capital social": selected.get("share_capital") if confirmed else None,
        "Receita - CNAE principal": primary_cnae,
        "Receita - CNAE principal (descrição)": cnae_descriptions.get(primary_cnae) if primary_cnae else None,
        "Receita - CNAEs secundários": ", ".join(secondary),
        "Receita - CNAEs secundários (descrições)": " | ".join(filter(None, (cnae_descriptions.get(code) for code in secondary))),
        "Receita - Endereço": selected.get("address") if confirmed else None,
        "Receita - Município": selected.get("municipality") if confirmed else None,
        "Receita - UF": selected.get("uf") if confirmed else None,
        "Receita - CEP": selected.get("postal_code") if confirmed else None,
        "Simples - Disponível na base": "SIM" if simple else ("NÃO" if confirmed else None),
        "Simples - Optante": yes_no(simple.get("simples_option") if simple else None) if confirmed else None,
        "Simples - Data de opção": simple.get("simples_started_at") if simple else None,
        "Simples - Data de exclusão": simple.get("simples_ended_at") if simple else None,
        "MEI - Optante": yes_no(simple.get("mei_option") if simple else None) if confirmed else None,
        "MEI - Data de opção": simple.get("mei_started_at") if simple else None,
        "MEI - Data de exclusão": simple.get("mei_ended_at") if simple else None,
        "Receita - Versão da base": match.get("dataset_version"),
        "Auditoria - Decisão humana": "PENDENTE" if match["status"] == "revisao" else "NÃO NECESSÁRIA",
    })
    enriched.append(row)

    for rank, candidate in enumerate(match.get("candidates", []), start=1):
        candidate_signals = candidate.get("signals", {})
        audit.append({
            "Linha original": match["row_number"],
            "Apollo Account Id": original.get("Apollo Account Id"),
            "Empresa informada": original.get("Company Name"),
            "Status do match": status_labels[match["status"]],
            "Posição do candidato": rank,
            "CNPJ candidato": format_cnpj(candidate.get("cnpj")),
            "Razão social candidata": candidate.get("legal_name"),
            "Nome fantasia candidato": candidate.get("trade_name"),
            "Score": candidate.get("score"),
            "Confiança final": match["confidence"] if rank == 1 else None,
            "Similaridade do nome": candidate_signals.get("nome"),
            "CEP coincide": yes_no(candidate_signals.get("cep"), "NÃO"),
            "Município coincide": yes_no(candidate_signals.get("municipio"), "NÃO"),
            "Similaridade do logradouro": candidate_signals.get("logradouro"),
            "Número coincide": yes_no(candidate_signals.get("numero"), "NÃO"),
            "CNPJ extraído do site": yes_no(candidate_signals.get("cnpj_extraido_do_site"), "NÃO"),
            "Situação cadastral": candidate.get("registration_status"),
            "Município Receita": candidate.get("municipality"),
            "UF Receita": candidate.get("uf"),
            "CEP Receita": candidate.get("postal_code"),
            "Versão Receita": match.get("dataset_version"),
        })

    if match["status"] == "revisao":
        candidates = match.get("candidates", [])
        review_row = {
            "Linha original": match["row_number"],
            "Apollo Account Id": original.get("Apollo Account Id"),
            "Empresa informada": original.get("Company Name"),
            "Website": original.get("Website"),
            "Endereço informado": original.get("Company Address"),
            "Decisão humana": "PENDENTE",
            "CNPJ escolhido": None,
            "Observação": None,
        }
        for rank in range(1, 4):
            candidate = candidates[rank - 1] if len(candidates) >= rank else {}
            review_row[f"Candidato {rank} - CNPJ"] = format_cnpj(candidate.get("cnpj"))
            review_row[f"Candidato {rank} - Razão social"] = candidate.get("legal_name")
            review_row[f"Candidato {rank} - Score"] = candidate.get("score")
        review.append(review_row)

partners = []
confirmed_by_root = {result["selected"]["cnpj_root"]: result for result in matches if result["status"] == "confirmado" and result.get("selected")}
for partner in auxiliary["partners"]:
    root = partner["cnpj_root"]
    if root not in confirmed_roots:
        continue
    match = confirmed_by_root[root]
    original = original_rows[match["row_number"] - 1]
    partners.append({
        "Linha original": match["row_number"],
        "Empresa informada": original.get("Company Name"),
        "CNPJ da empresa": format_cnpj(match["selected"]["cnpj"]),
        "Razão social da empresa": match["selected"].get("legal_name"),
        "Tipo de sócio": partner.get("partner_type"),
        "Nome / razão social do sócio": partner.get("partner_name"),
        "CPF mascarado / CNPJ do sócio": partner.get("partner_document"),
        "Qualificação (código)": partner.get("qualification_code"),
        "Qualificação": partner.get("qualification"),
        "Data de entrada": partner.get("joined_at"),
        "País (código)": partner.get("country_code"),
        "País": partner.get("country"),
        "Faixa etária": partner.get("age_range"),
        "Representante legal - documento": partner.get("legal_representative_document"),
        "Representante legal - nome": partner.get("legal_representative_name"),
        "Representante legal - qualificação": partner.get("legal_representative_qualification"),
        "Empresa sócia foi enriquecida?": "NÃO" if partner.get("partner_type_code") == "1" else "NÃO SE APLICA",
        "Versão Receita": auxiliary["dataset_version"],
    })

summary = Counter(match["status"] for match in matches)
payload = {
    "source_headers": original_headers,
    "enriched": enriched,
    "partners": partners,
    "audit": audit,
    "review": review,
    "summary": {
        "total": len(matches),
        "confirmed": summary["confirmado"],
        "review": summary["revisao"],
        "not_found": summary["nao_encontrado"],
        "partners": len(partners),
        "simples_available": sum(1 for row in enriched if row.get("Simples - Disponível na base") == "SIM"),
        "dataset_version": auxiliary["dataset_version"],
    },
}
OUTPUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print(json.dumps(payload["summary"], ensure_ascii=False))
