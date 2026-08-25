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


RELATION_CATALOG = [
    {
        "name": "rfb_establishments",
        "label": "Estabelecimentos consolidados",
        "group": "ready",
        "description": "Uma linha por CNPJ, com nomes, situação, CNAEs, endereço e versão da Receita.",
        "recommended": True,
    },
    {
        "name": "rfb_current_company_details",
        "label": "Empresa vigente",
        "group": "ready",
        "description": "Uma linha por raiz de CNPJ com natureza jurídica, responsável, porte e capital.",
        "recommended": True,
    },
    {
        "name": "rfb_current_establishment_details",
        "label": "Complementos do estabelecimento vigentes",
        "group": "ready",
        "description": "Matriz ou filial, contatos, motivos cadastrais, exterior e situação especial.",
        "recommended": True,
    },
    {
        "name": "rfb_current_simples",
        "label": "Simples e MEI vigentes",
        "group": "ready",
        "description": "Opção e exclusão do Simples Nacional e do SIMEI por raiz de CNPJ.",
        "recommended": True,
    },
    {
        "name": "rfb_current_partners",
        "label": "Sócios e administradores vigentes",
        "group": "ready",
        "description": "Quadro societário público, qualificações, entrada, faixa etária e representante legal.",
        "recommended": True,
    },
    {
        "name": "rfb_current_company_branch_counts",
        "label": "Contagem de matriz e filiais vigente",
        "group": "ready",
        "description": "Quantidade de estabelecimentos e filiais ativas por raiz de CNPJ.",
        "recommended": True,
    },
    {
        "name": "rfb_current_aux_reference",
        "label": "Dicionários oficiais vigentes",
        "group": "ready",
        "description": "Tradução dos códigos de CNAE, natureza jurídica, país, município e qualificação.",
        "recommended": True,
    },
    {
        "name": "rfb_company_details",
        "label": "Histórico de dados da empresa",
        "group": "storage",
        "description": "Dados da empresa separados por versão da Receita e raiz de CNPJ.",
    },
    {
        "name": "rfb_establishment_details",
        "label": "Histórico de complementos",
        "group": "storage",
        "description": "Campos complementares dos estabelecimentos separados por versão.",
    },
    {
        "name": "rfb_simples",
        "label": "Histórico de Simples e MEI",
        "group": "storage",
        "description": "Registros brutos do Simples e SIMEI separados por versão.",
    },
    {
        "name": "rfb_partners",
        "label": "Histórico de sócios",
        "group": "storage",
        "description": "Registros brutos do quadro societário separados por versão.",
    },
    {
        "name": "rfb_company_branch_counts",
        "label": "Histórico de contagens de filiais",
        "group": "storage",
        "description": "Resumo de matriz e filiais por empresa e versão.",
    },
    {
        "name": "rfb_aux_reference",
        "label": "Histórico dos dicionários",
        "group": "storage",
        "description": "Descrições oficiais dos códigos, preservadas por versão.",
    },
    {
        "name": "dataset_versions",
        "label": "Versões da base principal",
        "group": "operations",
        "description": "Controle da versão consolidada, situação e quantidade de estabelecimentos.",
    },
    {
        "name": "rfb_aux_datasets",
        "label": "Versões da carga complementar",
        "group": "operations",
        "description": "Estado geral da carga de sócios, Simples, contatos e dicionários.",
    },
    {
        "name": "rfb_aux_import_files",
        "label": "Arquivos da carga complementar",
        "group": "operations",
        "description": "Progresso, linhas carregadas e retomada de cada arquivo oficial.",
    },
    {
        "name": "rfb_municipalities",
        "label": "Mapa de municípios",
        "group": "operations",
        "description": "Relação técnica entre código oficial, município normalizado e UF.",
    },
]

RELATION_CATALOG_BY_NAME = {relation["name"]: relation for relation in RELATION_CATALOG}


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
