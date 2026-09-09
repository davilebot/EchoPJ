"""Pure SQL builder for company discovery filters.

All dynamic values are parameters.  The only interpolated fragments come from
fixed capability/field maps controlled by the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from plataforma_receita.normalization import digits, normalize


REGION_STATES = {
    "N": ("AC", "AP", "AM", "PA", "RO", "RR", "TO"),
    "NE": ("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"),
    "CO": ("DF", "GO", "MT", "MS"),
    "SE": ("ES", "MG", "RJ", "SP"),
    "S": ("PR", "RS", "SC"),
}
ALL_STATES = tuple(sorted({state for states in REGION_STATES.values() for state in states}))

COMPANY_SIZE_CODES = {
    "NAO INFORMADO": "00",
    "MICRO EMPRESA": "01",
    "EMPRESA DE PEQUENO PORTE": "03",
    "DEMAIS": "05",
}


@dataclass(frozen=True)
class SearchCapabilities:
    simples: bool = False
    company_details: bool = False
    establishment_details: bool = False
    partners: bool = False
    references: bool = False
    branch_counts: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "simples_mei": self.simples,
            "legal_nature": self.company_details,
            "establishment_details": self.establishment_details,
            "partners": self.partners,
            "references": self.references,
            "branch_counts": self.branch_counts,
        }


class SearchCapabilityUnavailable(ValueError):
    pass


def _selected_states(filters: dict[str, Any]) -> tuple[str, ...] | None:
    regions = set(filters.get("regions") or [])
    if filters.get("region"):
        regions.add(filters["region"])
    region_states = {
        state for region in regions for state in REGION_STATES[region]
    } if regions else None
    requested = {str(value).upper() for value in (filters.get("ufs") or [])}
    if requested and region_states is not None:
        selected = requested & region_states
        if not selected:
            raise ValueError("as UFs selecionadas nao pertencem a regiao informada")
        return tuple(sorted(selected))
    if requested:
        return tuple(sorted(requested))
    return tuple(sorted(region_states)) if region_states is not None else None


def build_search_query(
    filters: dict[str, Any],
    capabilities: SearchCapabilities,
    *,
    count_only: bool = False,
    candidate_only: bool = False,
) -> tuple[str, list[Any]]:
    predicates: list[str] = []
    parameters: list[Any] = []
    joins: list[str] = []
    filter_joins: list[str] = []
    statuses = filters.get("registration_statuses") or []
    active_only = not statuses or set(statuses) == {"ATIVA"}
    capital_filtered = (
        filters.get("share_capital_min") is not None
        or filters.get("share_capital_max") is not None
    )

    needs_simples = filters.get("simples") is not None or filters.get("mei") is not None
    needs_company = bool(filters.get("legal_nature_code"))
    needs_partners = bool(filters.get("partner_age_ranges"))
    needs_establishment = any(
        filters.get(field) is not None
        for field in ("branch_type", "has_email", "has_phone")
    )
    needs_branch_counts = any(
        filters.get(field) is not None
        for field in ("active_branch_count_min", "active_branch_count_max")
    )
    needs_company_for_inactive_filter = not active_only and (
        bool(filters.get("company_sizes"))
        or filters.get("share_capital_min") is not None
        or filters.get("share_capital_max") is not None
    )
    needs_establishment_for_inactive_filter = not active_only and (
        bool(filters.get("cnae") or filters.get("cnaes") or filters.get("excluded_cnaes"))
        or filters.get("opened_from") is not None
        or filters.get("opened_to") is not None
    )
    if needs_simples and not capabilities.simples:
        raise SearchCapabilityUnavailable("Simples e MEI aguardam a carga complementar da Receita")
    if needs_company and not capabilities.company_details:
        raise SearchCapabilityUnavailable("natureza juridica aguarda a carga complementar da Receita")
    if needs_establishment and not capabilities.establishment_details:
        raise SearchCapabilityUnavailable("matriz/filial e contatos aguardam a carga complementar da Receita")
    if needs_partners and not capabilities.partners:
        raise SearchCapabilityUnavailable("socios e faixas etarias aguardam a carga complementar da Receita")
    if needs_branch_counts and not capabilities.branch_counts:
        raise SearchCapabilityUnavailable("o resumo de filiais ainda esta sendo preparado")

    if capabilities.simples and needs_simples:
        simples_join = (
            "LEFT JOIN rfb_simples s ON s.cnpj_root=e.cnpj_root "
            "AND s.dataset_version=e.dataset_version"
        )
        joins.append(simples_join)
        if needs_simples:
            filter_joins.append(simples_join)
        simples_columns = "s.is_simples,s.is_mei"
    else:
        simples_columns = "NULL::boolean AS is_simples,NULL::boolean AS is_mei"
    base_company_size_expression = (
        "CASE e.company_size "
        "WHEN '00' THEN 'NAO INFORMADO' "
        "WHEN '01' THEN 'MICRO EMPRESA' "
        "WHEN '03' THEN 'EMPRESA DE PEQUENO PORTE' "
        "WHEN '05' THEN 'DEMAIS' "
        "ELSE coalesce(nullif(e.company_size,''),'NAO INFORMADO') END"
    )
    if capabilities.company_details and (needs_company or needs_company_for_inactive_filter):
        company_join = (
            "LEFT JOIN rfb_company_details c ON c.cnpj_root=e.cnpj_root "
            "AND c.dataset_version=e.dataset_version"
        )
        joins.append(company_join)
        if needs_company or needs_company_for_inactive_filter:
            filter_joins.append(company_join)
        legal_nature_column = "c.legal_nature_code"
        company_size_expression = f"coalesce(c.company_size,{base_company_size_expression})"
        company_size_filter_expression = "e.company_size" if active_only else "coalesce(c.company_size_code,e.company_size)"
        share_capital_expression = "e.share_capital" if active_only else "coalesce(e.share_capital,c.share_capital)"
    else:
        legal_nature_column = "NULL::text AS legal_nature_code"
        company_size_expression = base_company_size_expression
        company_size_filter_expression = "e.company_size"
        share_capital_expression = "e.share_capital"
    if capabilities.establishment_details and (needs_establishment or needs_establishment_for_inactive_filter):
        establishment_join = (
            "LEFT JOIN rfb_establishment_details x ON x.cnpj=e.cnpj "
            "AND x.dataset_version=e.dataset_version"
        )
        joins.append(establishment_join)
        if needs_establishment or needs_establishment_for_inactive_filter:
            filter_joins.append(establishment_join)
        detail_columns = "x.branch_type_code,x.email,x.phone1_area_code,x.phone1"
        if active_only:
            opened_expression = "e.opened_at"
            cnae_expression = "e.primary_cnae"
            secondary_cnaes_expression = "e.secondary_cnaes"
            address_expressions = {
                field: f"e.{field}"
                for field in ("street_type", "street", "street_number", "address_extra", "district")
            }
        else:
            opened_expression = "coalesce(e.opened_at,x.opened_at)"
            cnae_expression = "coalesce(e.primary_cnae,x.primary_cnae)"
            secondary_cnaes_expression = "coalesce(e.secondary_cnaes,x.secondary_cnaes)"
            address_expressions = {
                field: f"coalesce(e.{field},x.{field})"
                for field in ("street_type", "street", "street_number", "address_extra", "district")
            }
    else:
        detail_columns = (
            "NULL::text AS branch_type_code,NULL::text AS email,"
            "NULL::text AS phone1_area_code,NULL::text AS phone1"
        )
        opened_expression = "e.opened_at"
        cnae_expression = "e.primary_cnae"
        secondary_cnaes_expression = "e.secondary_cnaes"
        address_expressions = {
            field: f"e.{field}"
            for field in ("street_type", "street", "street_number", "address_extra", "district")
        }

    if capabilities.branch_counts and needs_branch_counts:
        branch_counts_join = (
            "LEFT JOIN rfb_company_branch_counts b ON b.cnpj_root=e.cnpj_root "
            "AND b.dataset_version=e.dataset_version"
        )
        joins.append(branch_counts_join)
        if needs_branch_counts:
            filter_joins.append(branch_counts_join)
        branch_count_columns = (
            "coalesce(b.branch_count,0) AS branch_count,"
            "coalesce(b.active_branch_count,0) AS active_branch_count"
        )
    else:
        branch_count_columns = "0::integer AS branch_count,0::integer AS active_branch_count"

    if set(statuses) == {"ATIVA"}:
        predicates.append("e.is_active")
    elif statuses:
        predicates.append("e.registration_status=ANY(%s)")
        parameters.append(statuses)
    else:
        predicates.append("e.is_active")

    states = _selected_states(filters)
    if states:
        if len(states) == 1:
            predicates.append("e.uf=%s")
            parameters.append(states[0])
        else:
            predicates.append("e.uf=ANY(%s)")
            parameters.append(list(states))
    elif active_only and capital_filtered:
        # The partition indexes start with UF. Making the nationwide scope
        # explicit lets PostgreSQL seek directly into the capital range.
        predicates.append("e.uf=ANY(%s)")
        parameters.append(list(ALL_STATES))

    municipality_pairs: dict[str, list[str]] = {}
    municipalities: list[str] = []
    for value in (filters.get("municipalities") or []):
        raw = str(value)
        if "|" in raw:
            uf, municipality = raw.split("|", 1)
            municipality_pairs.setdefault(uf, []).append(normalize(municipality))
        else:
            municipalities.append(normalize(raw))
    legacy_municipality = normalize(filters.get("municipality"))
    if legacy_municipality:
        municipalities.append(legacy_municipality)
    municipalities = list(dict.fromkeys(value for value in municipalities if value))
    municipality_clauses: list[str] = []
    if municipalities:
        municipality_clauses.append("e.municipality=ANY(%s)")
        parameters.append(municipalities)
    for uf, names in municipality_pairs.items():
        municipality_clauses.append("(e.uf=%s AND e.municipality=ANY(%s))")
        parameters.extend([uf, list(dict.fromkeys(names))])
    if municipality_clauses:
        predicates.append(f"({' OR '.join(municipality_clauses)})")

    postal_codes = [digits(value) for value in (filters.get("postal_code_prefixes") or [])]
    legacy_postal_code = digits(filters.get("postal_code_prefix"))
    if legacy_postal_code:
        postal_codes.append(legacy_postal_code)
    postal_codes = list(dict.fromkeys(value for value in postal_codes if value))
    if postal_codes:
        patterns = [f"{postal_code}%" for postal_code in postal_codes]
        if len(patterns) == 1:
            predicates.append("e.postal_code LIKE %s")
            parameters.append(patterns[0])
        else:
            predicates.append("e.postal_code LIKE ANY(%s)")
            parameters.append(patterns)

    cnaes = [digits(value) for value in (filters.get("cnaes") or [])]
    legacy_cnae = digits(filters.get("cnae"))
    if legacy_cnae:
        cnaes.append(legacy_cnae)
    cnaes = list(dict.fromkeys(value for value in cnaes if value))
    if cnaes:
        if filters.get("cnae_scope") == "any":
            if len(cnaes) == 1:
                predicates.append(f"({cnae_expression}=%s OR {secondary_cnaes_expression} @> ARRAY[%s]::text[])")
                parameters.extend([cnaes[0], cnaes[0]])
            else:
                predicates.append(f"({cnae_expression}=ANY(%s) OR {secondary_cnaes_expression} && %s)")
                parameters.extend([cnaes, cnaes])
        elif all(len(cnae) == 7 for cnae in cnaes):
            if len(cnaes) == 1:
                predicates.append(f"{cnae_expression}=%s")
                parameters.append(cnaes[0])
            else:
                predicates.append(f"{cnae_expression}=ANY(%s)")
                parameters.append(cnaes)
        else:
            patterns = [f"{cnae}%" for cnae in cnaes]
            if len(patterns) == 1:
                predicates.append(f"{cnae_expression} LIKE %s")
                parameters.append(patterns[0])
            else:
                predicates.append(f"{cnae_expression} LIKE ANY(%s)")
                parameters.append(patterns)

    excluded_cnaes = [digits(value) for value in (filters.get("excluded_cnaes") or [])]
    excluded_cnaes = list(dict.fromkeys(value for value in excluded_cnaes if value))
    if excluded_cnaes:
        predicates.append(
            f"NOT ({cnae_expression}=ANY(%s) OR "
            f"coalesce({secondary_cnaes_expression},ARRAY[]::text[]) && %s)"
        )
        parameters.extend([excluded_cnaes, excluded_cnaes])

    sizes = filters.get("company_sizes") or []
    if sizes:
        predicates.append(f"{company_size_filter_expression}=ANY(%s)")
        parameters.append([COMPANY_SIZE_CODES[size] for size in sizes])

    company_name = normalize(filters.get("company_name"))
    if company_name:
        predicates.append("(e.normalized_legal_name LIKE %s OR e.normalized_trade_name LIKE %s)")
        name_pattern = f"%{company_name}%"
        parameters.extend([name_pattern, name_pattern])

    excluded_names = [normalize(value) for value in (filters.get("excluded_company_names") or [])]
    excluded_patterns = [f"%{value}%" for value in dict.fromkeys(value for value in excluded_names if value)]
    if excluded_patterns:
        predicates.append(
            "NOT (coalesce(e.normalized_legal_name,'') LIKE ANY(%s) "
            "OR coalesce(e.normalized_trade_name,'') LIKE ANY(%s))"
        )
        parameters.extend([excluded_patterns, excluded_patterns])

    if filters.get("partner_age_ranges"):
        predicates.append("""
            EXISTS (
              SELECT 1 FROM rfb_partners partner_age
              WHERE partner_age.dataset_version=e.dataset_version
                AND partner_age.cnpj_root=e.cnpj_root
                AND partner_age.age_range_code=ANY(%s)
            )
        """)
        parameters.append(filters["partner_age_ranges"])

    for field, operator in (
        ("share_capital_min", ">="),
        ("share_capital_max", "<="),
        ("opened_from", ">="),
        ("opened_to", "<="),
    ):
        if filters.get(field) is not None:
            database_expression = share_capital_expression if field.startswith("share_capital") else opened_expression
            predicates.append(f"{database_expression}{operator}%s")
            parameters.append(filters[field])

    if filters.get("simples") is not None:
        predicates.append("s.is_simples=%s")
        parameters.append(filters["simples"])
    if filters.get("mei") is not None:
        predicates.append("s.is_mei=%s")
        parameters.append(filters["mei"])
    if filters.get("legal_nature_code"):
        predicates.append("c.legal_nature_code=%s")
        parameters.append(filters["legal_nature_code"])
    if filters.get("branch_type") is not None:
        predicates.append("x.branch_type_code=%s")
        parameters.append(filters["branch_type"])
    if filters.get("has_email") is True:
        predicates.append("nullif(x.email,'') IS NOT NULL")
    elif filters.get("has_email") is False:
        predicates.append("nullif(x.email,'') IS NULL")
    if filters.get("has_phone") is True:
        predicates.append("nullif(x.phone1,'') IS NOT NULL")
    elif filters.get("has_phone") is False:
        predicates.append("nullif(x.phone1,'') IS NULL")
    if filters.get("active_branch_count_min") is not None:
        predicates.append("coalesce(b.active_branch_count,0)>=%s")
        parameters.append(filters["active_branch_count_min"])
    if filters.get("active_branch_count_max") is not None:
        predicates.append("coalesce(b.active_branch_count,0)<=%s")
        parameters.append(filters["active_branch_count_max"])

    if count_only:
        return f"""
            SELECT count(*) AS total_count
            FROM rfb_establishments e
            {' '.join(filter_joins)}
            WHERE {' AND '.join(predicates)}
        """, parameters

    limit = int(filters["limit"])
    order_expression = "e.share_capital,e.cnpj" if active_only and capital_filtered else "e.cnpj"
    parameters.append(limit)
    if candidate_only:
        return f"""
            SELECT e.uf,e.cnpj,e.share_capital
            FROM rfb_establishments e
            {' '.join(filter_joins)}
            WHERE {' AND '.join(predicates)}
            ORDER BY {order_expression}
            LIMIT %s
        """, parameters
    sql = f"""
        WITH matched AS MATERIALIZED (
          SELECT e.*
          FROM rfb_establishments e
          {' '.join(filter_joins)}
          WHERE {' AND '.join(predicates)}
          ORDER BY {order_expression}
          LIMIT %s
        )
        SELECT
          e.cnpj,e.cnpj_root,e.legal_name,e.trade_name,e.registration_status,
          e.registration_status_date,{opened_expression} AS opened_at,
          {company_size_expression} AS company_size,{share_capital_expression} AS share_capital,
          {cnae_expression} AS primary_cnae,{secondary_cnaes_expression} AS secondary_cnaes,
          e.municipality,e.uf,e.postal_code,
          {address_expressions['street_type']} AS street_type,
          {address_expressions['street']} AS street,
          {address_expressions['street_number']} AS street_number,
          {address_expressions['address_extra']} AS address_extra,
          {address_expressions['district']} AS district,
          e.dataset_version,{simples_columns},{legal_nature_column},{detail_columns},
          {branch_count_columns}
        FROM matched e
        {' '.join(joins)}
        ORDER BY {order_expression}
    """
    return sql, parameters


def build_search_count_query(
    filters: dict[str, Any],
    capabilities: SearchCapabilities,
) -> tuple[str, list[Any]]:
    """Build the matching count without forcing the result query to scan every row.

    Keeping the count separate lets PostgreSQL stop the ordered result query at
    10,000 rows while still reporting the exact size of the full filtered set.
    """
    return build_search_query(filters, capabilities, count_only=True)


def build_search_candidate_query(
    filters: dict[str, Any],
    capabilities: SearchCapabilities,
) -> tuple[str, list[Any]]:
    """Build a lightweight ordered query used to merge partition results."""
    return build_search_query(filters, capabilities, candidate_only=True)
