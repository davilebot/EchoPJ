from typing import Any

from .normalization import LEGAL_WORDS, STREET_WORDS, address_number, digits, normalize, similarity


WEB_PAGE_WORDS = {
    "HOME", "INICIO", "CONTATO", "CONTACT", "POLITICA", "PRIVACIDADE",
    "PRIVACY", "TERMOS", "CONDICOES", "QUEM", "SOMOS", "PAGINA", "SITE",
}


def score_candidate(item: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    legal_similarity = similarity(item.get("company_name"), candidate.get("legal_name"), LEGAL_WORDS)
    trade_similarity = similarity(item.get("company_name"), candidate.get("trade_name"), LEGAL_WORDS)
    site_name_similarity = max(
        [similarity(name, candidate.get("legal_name"), LEGAL_WORDS | WEB_PAGE_WORDS) for name in item.get("site_names", [])]
        + [similarity(name, candidate.get("trade_name"), LEGAL_WORDS | WEB_PAGE_WORDS) for name in item.get("site_names", [])]
        + [0.0]
    )
    name_similarity = max(legal_similarity, trade_similarity, site_name_similarity)
    name_points = round(55 * name_similarity)

    input_cep = digits(item.get("postal_code"))
    candidate_cep = digits(candidate.get("postal_code"))
    cep_match = bool(len(input_cep) == 8 and input_cep == candidate_cep)
    cep_conflict = bool(len(input_cep) == 8 and len(candidate_cep) == 8 and input_cep != candidate_cep)

    input_city = normalize(item.get("city"))
    candidate_city = normalize(candidate.get("municipality"))
    city_match = bool(input_city and candidate_city and input_city == candidate_city)
    city_conflict = bool(input_city and candidate_city and input_city != candidate_city)

    # O campo de endereco completo do Apollo termina com cidade/UF/CEP. Para
    # comparar numero e logradouro, a coluna especifica de rua e mais confiavel.
    input_address = item.get("street") or item.get("address")
    street_similarity = similarity(input_address, candidate.get("address"), STREET_WORDS)
    street_match = street_similarity >= 0.55
    input_number = address_number(input_address)
    candidate_number = address_number(candidate.get("address"))
    number_match = bool(input_number and candidate_number and input_number == candidate_number)
    number_conflict = bool(input_number and candidate_number and input_number != candidate_number)

    site_cnpj = candidate.get("cnpj") in set(item.get("site_cnpjs", []))
    provided_cnpj = bool(digits(item.get("cnpj")) and digits(item.get("cnpj")) == candidate.get("cnpj"))
    direct_cnpj = site_cnpj or provided_cnpj
    exact_name = bool(normalize(item.get("company_name")) and normalize(item.get("company_name")) in {
        normalize(candidate.get("legal_name")), normalize(candidate.get("trade_name"))
    })

    points = name_points
    points += 15 if cep_match else 0
    points += 10 if city_match else 0
    points += round(10 * street_similarity)
    points += 5 if number_match else 0
    points += 5 if exact_name else 0
    points -= 25 if city_conflict else 0
    points -= 12 if cep_conflict and not street_match else 0
    points -= 5 if number_conflict and street_similarity < 0.7 else 0
    score = max(0, min(100, points))

    location_matches = sum([cep_match, city_match, street_match, number_match])
    if direct_cnpj:
        if provided_cnpj:
            score = 100
        elif name_similarity >= 0.55 or (name_similarity >= 0.35 and location_matches >= 2):
            score = max(score, 94)
        elif location_matches >= 2:
            score = max(score, 78)

    signals = {
        "cnpj_extraido_do_site": site_cnpj,
        "cnpj_informado_validado": provided_cnpj,
        "nome": round(name_similarity, 3),
        "nome_legal": round(legal_similarity, 3),
        "nome_fantasia": round(trade_similarity, 3),
        "nome_no_site": round(site_name_similarity, 3),
        "cep": cep_match,
        "municipio": city_match,
        "logradouro": round(street_similarity, 3),
        "numero": number_match,
        "conflito_cep": cep_conflict,
        "conflito_municipio": city_conflict,
        "conflito_numero": number_conflict,
        "sinais_localizacao": location_matches,
    }
    return {**candidate, "score": score, "signals": signals}


def decide(item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted((score_candidate(item, candidate) for candidate in candidates), key=lambda row: (-row["score"], row["cnpj"]))
    top = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    margin = top["score"] - runner_up["score"] if top and runner_up else 100

    status = "nao_encontrado"
    confidence = 0
    selected = None
    if top:
        signals = top["signals"]
        direct = signals["cnpj_extraido_do_site"]
        provided = signals["cnpj_informado_validado"]
        name_similarity = signals["nome"]
        location_matches = signals["sinais_localizacao"]
        no_major_conflict = not signals["conflito_municipio"]
        strict_generic = (
            top["score"] >= 82
            and name_similarity >= 0.62
            and location_matches >= 2
            and margin >= 8
            and no_major_conflict
        ) or (
            top["score"] >= 88
            and name_similarity >= 0.78
            and location_matches >= 1
            and margin >= 8
            and no_major_conflict
        )
        strict_direct = direct and top["score"] >= 90 and name_similarity >= 0.35 and no_major_conflict
        if provided or strict_direct or strict_generic:
            status = "confirmado"
            confidence = 5 if provided or (top["score"] >= 92 and margin >= 12) else 4
            selected = top
        elif top["score"] >= 58 and name_similarity >= 0.32:
            status = "revisao"
            confidence = 3 if top["score"] >= 72 and margin >= 5 else 2
            selected = top

    for candidate in ranked:
        candidate["margem_para_segundo"] = margin if candidate is top else None
    return {
        "row_number": item["row_number"],
        "status": status,
        "confidence": confidence,
        "selected": selected,
        "candidates": ranked[:3],
    }
