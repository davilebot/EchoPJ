"""Small, safe helpers for the business-oriented database explorer."""

from __future__ import annotations

import re


FIELD_GROUPS = [
    {
        "key": "establishments",
        "label": "Empresas e estabelecimentos",
        "description": "CNPJ, nomes, situação, abertura, porte, capital, CNAEs e endereço.",
    },
    {
        "key": "companies",
        "label": "Dados da empresa",
        "description": "Natureza jurídica, responsável, porte, capital e ente federativo por raiz do CNPJ.",
    },
    {
        "key": "simples",
        "label": "Simples Nacional e MEI",
        "description": "Opção, exclusão e respectivas datas do Simples e do SIMEI.",
    },
    {
        "key": "partners",
        "label": "Sócios e administradores",
        "description": "Nome, tipo, qualificação, entrada, faixa etária e representante legal.",
    },
    {
        "key": "establishment_details",
        "label": "Contatos e complementos",
        "description": "Matriz/filial, motivo cadastral, telefone, e-mail, exterior e situação especial.",
    },
    {
        "key": "references",
        "label": "Dicionários oficiais",
        "description": "Descrições de CNAEs, naturezas, qualificações, países, municípios e motivos.",
    },
]


def normalize_cnpj_identifier(value: str | None) -> str:
    normalized = re.sub(r"[^0-9A-Z]", "", (value or "").upper())
    if len(normalized) != 14:
        raise ValueError("informe um CNPJ com 14 caracteres")
    return normalized


def cnpj_root_bounds(value: str | None) -> tuple[str, str, str]:
    """Return an indexed CNPJ range for every establishment of one company root."""
    normalized = normalize_cnpj_identifier(value)
    root = normalized[:8]
    return root, f"{root}000000", f"{root}ZZZZZZ"
