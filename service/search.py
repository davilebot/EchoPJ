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


@dataclass(frozen=True)
class SearchCapabilities:
    simples: bool = False
    company_details: bool = False
    establishment_details: bool = False
    partners: bool = False
    references: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "simples_mei": self.simples,
            "legal_nature": self.company_details,
            "establishment_details": self.establishment_details,
            "partners": self.partners,
            "references": self.references,
        }


class SearchCapabilityUnavailable(ValueError):
    pass


def _selected_states(filters: dict[str, Any]) -> tuple[str, ...] | None:
    region = filters.get("region")
    region_states = set(REGION_STATES[region]) if region else None
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
) -> tuple[str, list[Any]]:
    predicates: list[str] = []
    parameters: list[Any] = []
    joins: list[str] = []
    statuses = filters.get("registration_statuses") or []
    active_only = not statuses or set(statuses) == {"ATIVA"}

    needs_simples = filters.get("simples") is not None or filters.get("mei") is not None
    needs_company = bool(filters.get("legal_nature_code"))
    needs_establishment = any(
        filters.get(field) is not None
        for field in ("branch_type", "has_email", "has_phone")
    )
    if needs_simples and not capabilities.simples:
        raise SearchCapabilityUnavailable("Simples e MEI aguardam a carga complementar da Receita")
    if needs_company and not capabilities.company_details:
        raise SearchCapabilityUnavailable("natureza juridica aguarda a carga complementar da Receita")
    if needs_establishment and not capabilities.establishment_details:
        raise SearchCapabilityUnavailable("matriz/filial e contatos aguardam a carga complementar da Receita")

    if capabilities.simples:
        joins.append(
            "LEFT JOIN rfb_simples s ON s.cnpj_root=e.cnpj_root "
            "AND s.dataset_version=e.dataset_version"
        )
        simples_columns = "s.is_simples,s.is_mei"
    else:
        simples_columns = "NULL::boolean AS is_simples,NULL::boolean AS is_mei"
    if capabilities.company_details:
        joins.append(
            "LEFT JOIN rfb_company_details c ON c.cnpj_root=e.cnpj_root "
            "AND c.dataset_version=e.dataset_version"
        )
        legal_nature_column = "c.legal_nature_code"
        company_size_expression = "e.company_size" if active_only else "coalesce(e.company_size,c.company_size)"
        share_capital_expression = "e.share_capital" if active_only else "coalesce(e.share_capital,c.share_capital)"
    else:
        legal_nature_column = "NULL::text AS legal_nature_code"
        company_size_expression = "e.company_size"
        share_capital_expression = "e.share_capital"
    if capabilities.establishment_details:
        joins.append(
            "LEFT JOIN rfb_establishment_details x ON x.cnpj=e.cnpj "
            "AND x.dataset_version=e.dataset_version"
        )
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

    if set(statuses) == {"ATIVA"}:
        predicates.append("e.is_active")
    elif statuses:
        predicates.append("e.registration_status=ANY(%s)")
        parameters.append(statuses)
    else:
        predicates.append("e.is_active")

    states = _selected_states(filters)
    if states:
        predicates.append("e.uf=ANY(%s)")
        parameters.append(list(states))

    municipality = normalize(filters.get("municipality"))
    if municipality:
        predicates.append("e.municipality=%s")
        parameters.append(municipality)

    postal_code = digits(filters.get("postal_code_prefix"))
    if postal_code:
        predicates.append("e.postal_code LIKE %s")
        parameters.append(f"{postal_code}%")

    cnae = digits(filters.get("cnae"))
    if cnae:
        if filters.get("cnae_scope") == "any":
            predicates.append(f"({cnae_expression}=%s OR {secondary_cnaes_expression} @> ARRAY[%s]::text[])")
            parameters.extend([cnae, cnae])
        elif len(cnae) == 7:
            predicates.append(f"{cnae_expression}=%s")
            parameters.append(cnae)
        else:
            predicates.append(f"{cnae_expression} LIKE %s")
            parameters.append(f"{cnae}%")

    sizes = filters.get("company_sizes") or []
    if sizes:
        predicates.append(f"{company_size_expression}=ANY(%s)")
        parameters.append(sizes)

    company_name = normalize(filters.get("company_name"))
    if company_name:
        predicates.append("(e.normalized_legal_name LIKE %s OR e.normalized_trade_name LIKE %s)")
        name_pattern = f"%{company_name}%"
        parameters.extend([name_pattern, name_pattern])

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

    limit = int(filters["limit"])
    parameters.append(limit + 1)
    sql = f"""
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
          e.dataset_version,{simples_columns},{legal_nature_column},{detail_columns}
        FROM rfb_establishments e
        {' '.join(joins)}
        WHERE {' AND '.join(predicates)}
        ORDER BY e.cnpj
        LIMIT %s
    """
    return sql, parameters
