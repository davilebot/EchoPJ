"""Parsers for the public CNPJ files published by Receita Federal.

The official files are semicolon-separated, encoded as Latin-1 and do not have
headers.  This module keeps the positional layout in one place so the loader
does not spread magic indexes throughout the codebase.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable


PARTNER_TYPE_LABELS = {
    "1": "PESSOA JURIDICA",
    "2": "PESSOA FISICA",
    "3": "ESTRANGEIRO",
}

AGE_RANGE_LABELS = {
    "1": "0 A 12 ANOS",
    "2": "13 A 20 ANOS",
    "3": "21 A 30 ANOS",
    "4": "31 A 40 ANOS",
    "5": "41 A 50 ANOS",
    "6": "51 A 60 ANOS",
    "7": "61 A 70 ANOS",
    "8": "71 A 80 ANOS",
    "9": "MAIOR DE 80 ANOS",
}

COMPANY_SIZE_LABELS = {
    "00": "NAO INFORMADO",
    "01": "MICRO EMPRESA",
    "03": "EMPRESA DE PEQUENO PORTE",
    "05": "DEMAIS",
}

REFERENCE_KINDS = {
    "reference_cnaes": "cnae",
    "reference_countries": "country",
    "reference_legal_natures": "legal_nature",
    "reference_municipalities": "municipality",
    "reference_qualifications": "qualification",
    "reference_status_reasons": "status_reason",
}


def clean(value: str | None) -> str | None:
    parsed = (value or "").replace("\x00", "").strip()
    return parsed or None


def digits(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


def identifier(value: str | None) -> str:
    return "".join(character for character in (value or "").upper() if character.isalnum())


def date_or_none(value: str | None) -> str | None:
    parsed = digits(value)
    if len(parsed) != 8 or parsed == "00000000":
        return None
    return f"{parsed[:4]}-{parsed[4:6]}-{parsed[6:]}"


def decimal_or_none(value: str | None) -> Decimal | None:
    parsed = (value or "").strip().replace(".", "").replace(",", ".")
    if not parsed:
        return None
    try:
        return Decimal(parsed)
    except InvalidOperation:
        return None


def bool_option(value: str | None) -> bool | None:
    parsed = (value or "").strip().upper()
    if parsed == "S":
        return True
    if parsed == "N":
        return False
    return None


def row_hash(row: list[str]) -> bytes:
    canonical = "\x1f".join((value or "").strip() for value in row)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def company_details_row(row: list[str], version: str) -> tuple | None:
    """Canonical company fields, including values summarized for inactive CNPJs."""
    if len(row) < 7 or len(identifier(row[0])) != 8:
        return None
    return (
        version,
        identifier(row[0]),
        clean(row[2]),
        clean(row[3]),
        clean(row[5]),
        COMPANY_SIZE_LABELS.get((row[5] or "").strip(), "NAO INFORMADO"),
        decimal_or_none(row[4]),
        clean(row[6]),
    )


def establishment_details_row(row: list[str], version: str) -> tuple | None:
    if len(row) < 30:
        return None
    cnpj = identifier(row[0]) + identifier(row[1]) + digits(row[2])
    if len(cnpj) != 14:
        return None
    inactive = (row[5] or "").strip() != "02"
    return (
        version,
        cnpj,
        clean(row[3]),
        clean(row[7]),
        clean(row[8]),
        clean(row[9]),
        date_or_none(row[10]) if inactive else None,
        clean(row[11]) if inactive else None,
        [value for value in (clean(row[12]) or "").split(",") if value] if inactive else None,
        clean(row[13]) if inactive else None,
        clean(row[14]) if inactive else None,
        clean(row[15]) if inactive else None,
        clean(row[16]) if inactive else None,
        clean(row[17]) if inactive else None,
        clean(row[21]),
        clean(row[22]),
        clean(row[23]),
        clean(row[24]),
        clean(row[25]),
        clean(row[26]),
        clean(row[27]),
        clean(row[28]),
        date_or_none(row[29]),
    )


def simples_row(row: list[str], version: str) -> tuple | None:
    if len(row) < 7 or len(identifier(row[0])) != 8:
        return None
    return (
        version,
        identifier(row[0]),
        bool_option(row[1]),
        date_or_none(row[2]),
        date_or_none(row[3]),
        bool_option(row[4]),
        date_or_none(row[5]),
        date_or_none(row[6]),
    )


def partner_row(row: list[str], version: str) -> tuple | None:
    if len(row) < 11 or len(identifier(row[0])) != 8:
        return None
    return (
        version,
        row_hash(row),
        identifier(row[0]),
        clean(row[1]),
        PARTNER_TYPE_LABELS.get((row[1] or "").strip()),
        clean(row[2]),
        clean(row[3]),
        clean(row[4]),
        date_or_none(row[5]),
        clean(row[6]),
        clean(row[7]),
        clean(row[8]),
        clean(row[9]),
        clean(row[10]),
        AGE_RANGE_LABELS.get((row[10] or "").strip()),
    )


def reference_row(kind: str, row: list[str], version: str) -> tuple | None:
    if len(row) < 2 or kind not in REFERENCE_KINDS:
        return None
    code = clean(row[0])
    label = clean(row[1])
    if not code or not label:
        return None
    return version, REFERENCE_KINDS[kind], code, label


@dataclass(frozen=True)
class Layout:
    table: str
    columns: tuple[str, ...]
    conflict_columns: tuple[str, ...]
    parser: Callable[[list[str], str], tuple | None]


LAYOUTS = {
    "companies": Layout(
        "rfb_company_details",
        (
            "dataset_version",
            "cnpj_root",
            "legal_nature_code",
            "responsible_qualification_code",
            "company_size_code",
            "company_size",
            "share_capital",
            "federative_entity",
        ),
        ("dataset_version", "cnpj_root"),
        company_details_row,
    ),
    "establishments": Layout(
        "rfb_establishment_details",
        (
            "dataset_version",
            "cnpj",
            "branch_type_code",
            "registration_status_reason_code",
            "foreign_city_name",
            "country_code",
            "opened_at",
            "primary_cnae",
            "secondary_cnaes",
            "street_type",
            "street",
            "street_number",
            "address_extra",
            "district",
            "phone1_area_code",
            "phone1",
            "phone2_area_code",
            "phone2",
            "fax_area_code",
            "fax",
            "email",
            "special_status",
            "special_status_date",
        ),
        ("dataset_version", "cnpj"),
        establishment_details_row,
    ),
    "simples": Layout(
        "rfb_simples",
        (
            "dataset_version",
            "cnpj_root",
            "is_simples",
            "simples_started_at",
            "simples_ended_at",
            "is_mei",
            "mei_started_at",
            "mei_ended_at",
        ),
        ("dataset_version", "cnpj_root"),
        simples_row,
    ),
    "partners": Layout(
        "rfb_partners",
        (
            "dataset_version",
            "row_hash",
            "cnpj_root",
            "partner_type_code",
            "partner_type",
            "partner_name",
            "partner_document",
            "qualification_code",
            "joined_at",
            "country_code",
            "legal_representative_document",
            "legal_representative_name",
            "legal_representative_qualification_code",
            "age_range_code",
            "age_range",
        ),
        ("dataset_version", "row_hash"),
        partner_row,
    ),
}

for reference_kind in REFERENCE_KINDS:
    LAYOUTS[reference_kind] = Layout(
        "rfb_aux_reference",
        ("dataset_version", "kind", "code", "label"),
        ("dataset_version", "kind", "code"),
        lambda row, version, kind=reference_kind: reference_row(kind, row, version),
    )
