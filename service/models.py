from datetime import date
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from plataforma_receita.normalization import digits, normalize, valid_cnpj
from .explorer import normalize_cnpj_identifier


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class PasswordResetRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=8, max_length=1024)


class OrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Informe o nome da organização.")
        return value


class SignupRequest(OrganizationRequest):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("Informe um e-mail válido.")
        return value


class SignupVerificationRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class MemberRoleRequest(BaseModel):
    role: Literal["admin", "member", "viewer"]


class InvitationRequest(MemberRoleRequest):
    role: Literal["admin", "member", "viewer"] = "member"
    email: str = Field(min_length=3, max_length=254)
    send_email: bool = True

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("Informe um e-mail válido.")
        return value


class InvitationTokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class InvitationAcceptRequest(InvitationTokenRequest):
    password: str = Field(min_length=1, max_length=1024)


class AccountUpdateRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str | None = Field(default=None, min_length=8, max_length=1024)

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("informe um e-mail valido")
        return normalized


class BillingProfileUpdateRequest(BaseModel):
    plan_code: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    subscription_status: Literal["trialing", "active", "past_due", "canceled", "suspended"]
    unlimited_credits: bool = False

    @field_validator("plan_code", mode="before")
    @classmethod
    def normalize_plan(cls, value: str) -> str:
        return value.strip().casefold()


class CreditAdjustmentRequest(BaseModel):
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    description: str = Field(min_length=3, max_length=200)

    @field_validator("amount")
    @classmethod
    def nonzero_amount(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Informe uma quantidade diferente de zero.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("Explique o motivo do ajuste.")
        return value


class MatchInput(BaseModel):
    local_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    address: str | None = Field(default=None, max_length=600)
    municipality: str | None = Field(default=None, max_length=200)
    uf: str = Field(min_length=2, max_length=2)
    postal_code: str | None = Field(default=None, max_length=20)
    website: str | None = Field(default=None, max_length=500)
    cnpj: str | None = Field(default=None, max_length=30)
    source: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("uf")
    @classmethod
    def normalize_uf(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, value: str | None) -> str | None:
        if not value:
            return None
        normalized = digits(value)
        if not valid_cnpj(normalized):
            raise ValueError("CNPJ invalido")
        return normalized

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        if len(value) > 100:
            raise ValueError("maximo de 100 colunas no CSV")
        clean = {}
        for key, item in value.items():
            clean_key = str(key).strip()[:200]
            clean_value = None if item is None else str(item)[:5000]
            if clean_key:
                clean[clean_key] = clean_value
        return clean


class BatchRequest(BaseModel):
    items: list[MatchInput] = Field(min_length=1, max_length=200)
    active_only: bool = True
    check_website: bool = True


class JobRequest(BaseModel):
    filename: str = Field(default="consulta.csv", min_length=1, max_length=255)
    items: list[MatchInput] = Field(min_length=1, max_length=10000)
    active_only: bool = True
    check_website: bool = False


class WebsiteEvidence(BaseModel):
    status: str
    cnpjs: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    pages_checked: list[str] = Field(default_factory=list)
    cached: bool = False


class CompanyLookupRequest(BaseModel):
    cnpjs: list[str] = Field(min_length=1, max_length=10000)

    @field_validator("cnpjs")
    @classmethod
    def clean_cnpjs(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(str(item).strip()[:40] for item in value if str(item).strip()))
        if not cleaned:
            raise ValueError("informe pelo menos um CNPJ")
        return cleaned


REGISTRATION_STATUSES = {"ATIVA", "BAIXADA", "INAPTA", "NULA", "SUSPENSA", "NAO INFORMADA"}
COMPANY_SIZES = {"MICRO EMPRESA", "EMPRESA DE PEQUENO PORTE", "DEMAIS", "NAO INFORMADO"}
VALID_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}


class CompanySearchRequest(BaseModel):
    region: Literal["N", "NE", "CO", "SE", "S"] | None = None
    regions: list[Literal["N", "NE", "CO", "SE", "S"]] = Field(default_factory=list, max_length=5)
    ufs: list[str] = Field(default_factory=list, max_length=27)
    municipality: str | None = Field(default=None, max_length=200)
    municipalities: list[str] = Field(default_factory=list, max_length=1000)
    postal_code_prefix: str | None = Field(default=None, max_length=9)
    postal_code_prefixes: list[str] = Field(default_factory=list, max_length=1000)
    cnae: str | None = Field(default=None, max_length=10)
    cnaes: list[str] = Field(default_factory=list, max_length=2000)
    cnae_scope: Literal["primary", "any"] = "primary"
    registration_statuses: list[str] = Field(default_factory=list, max_length=6)
    company_sizes: list[str] = Field(default_factory=list, max_length=4)
    company_name: str | None = Field(default=None, max_length=200)
    excluded_company_names: list[str] = Field(default_factory=list, max_length=100)
    partner_age_ranges: list[str] = Field(default_factory=list, max_length=9)
    share_capital_min: Decimal | None = Field(default=None, ge=0)
    share_capital_max: Decimal | None = Field(default=None, ge=0)
    opened_from: date | None = None
    opened_to: date | None = None
    simples: bool | None = None
    mei: bool | None = None
    legal_nature_code: str | None = Field(default=None, max_length=10)
    branch_type: Literal["1", "2"] | None = None
    has_email: bool | None = None
    has_phone: bool | None = None
    active_branch_count_min: int | None = Field(default=None, ge=0, le=100000)
    active_branch_count_max: int | None = Field(default=None, ge=0, le=100000)
    limit: int = Field(default=500, ge=1, le=10000)

    @field_validator("company_name")
    @classmethod
    def validate_company_name(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        if len(value.strip()) < 3:
            raise ValueError("informe pelo menos 3 caracteres do nome")
        return value.strip()

    @field_validator("ufs")
    @classmethod
    def validate_ufs(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        invalid = set(normalized) - VALID_UFS
        if invalid:
            raise ValueError(f"UF invalida: {', '.join(sorted(invalid))}")
        return normalized

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip().upper() for item in value if item.strip()))

    @field_validator("municipalities")
    @classmethod
    def validate_municipalities(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            raw = str(item).strip()
            if not raw:
                continue
            if "|" in raw:
                uf, municipality = raw.split("|", 1)
                uf = uf.strip().upper()
                municipality = normalize(municipality)
                if uf not in VALID_UFS or not municipality:
                    raise ValueError("municipio selecionado invalido")
                normalized.append(f"{uf}|{municipality}")
            else:
                municipality = normalize(raw)
                if municipality:
                    normalized.append(municipality)
        return list(dict.fromkeys(normalized))

    @field_validator("cnaes")
    @classmethod
    def validate_cnaes(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(digits(item) for item in value if digits(item)))
        if any(len(item) != 7 for item in normalized):
            raise ValueError("os CNAEs selecionados devem ter 7 digitos")
        return normalized

    @field_validator("excluded_company_names")
    @classmethod
    def validate_excluded_company_names(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            term = str(item).strip()
            normalized = normalize(term)
            if not normalized or normalized in seen:
                continue
            if len(normalized) < 2:
                raise ValueError("cada nome ou marca excluida deve ter pelo menos 2 caracteres")
            cleaned.append(term[:200])
            seen.add(normalized)
        return cleaned

    @field_validator("partner_age_ranges")
    @classmethod
    def validate_partner_age_ranges(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if set(normalized) - set("123456789"):
            raise ValueError("faixa etaria de socio invalida")
        return normalized

    @field_validator("registration_statuses")
    @classmethod
    def validate_statuses(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        invalid = set(normalized) - REGISTRATION_STATUSES
        if invalid:
            raise ValueError("situacao cadastral invalida")
        return normalized

    @field_validator("company_sizes")
    @classmethod
    def validate_sizes(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        invalid = set(normalized) - COMPANY_SIZES
        if invalid:
            raise ValueError("porte de empresa invalido")
        return normalized

    @field_validator("cnae")
    @classmethod
    def validate_cnae(cls, value: str | None) -> str | None:
        if not value:
            return None
        normalized = digits(value)
        if len(normalized) < 2 or len(normalized) > 7:
            raise ValueError("CNAE deve ter de 2 a 7 digitos")
        return normalized

    @field_validator("postal_code_prefix")
    @classmethod
    def validate_postal_code(cls, value: str | None) -> str | None:
        if not value:
            return None
        normalized = digits(value)
        if len(normalized) < 2 or len(normalized) > 8:
            raise ValueError("CEP deve ter de 2 a 8 digitos")
        return normalized

    @field_validator("postal_code_prefixes")
    @classmethod
    def validate_postal_codes(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(digits(item) for item in value if digits(item)))
        if any(len(item) < 2 or len(item) > 8 for item in normalized):
            raise ValueError("cada CEP deve ter de 2 a 8 digitos")
        return normalized

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.share_capital_min is not None and self.share_capital_max is not None:
            if self.share_capital_min > self.share_capital_max:
                raise ValueError("capital minimo nao pode ser maior que o maximo")
        if self.opened_from and self.opened_to and self.opened_from > self.opened_to:
            raise ValueError("data inicial nao pode ser posterior a data final")
        if self.active_branch_count_min is not None and self.active_branch_count_max is not None:
            if self.active_branch_count_min > self.active_branch_count_max:
                raise ValueError("minimo de filiais nao pode ser maior que o maximo")
        if self.cnae_scope == "any" and self.cnae and len(self.cnae) != 7:
            raise ValueError("para incluir CNAEs secundarios, informe o codigo completo de 7 digitos")
        return self


class SavedSearchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filters: CompanySearchRequest
    result_count: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Informe um nome para a busca.")
        return value


class SavedSearchRunRequest(BaseModel):
    result_count: int = Field(ge=0, le=10000)


class CompanyListRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Informe um nome para a lista.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return " ".join(value.split())


class CompanySelectionRequest(BaseModel):
    cnpjs: list[str] = Field(min_length=1, max_length=10000)

    @field_validator("cnpjs")
    @classmethod
    def clean_cnpjs(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            try:
                normalized = normalize_cnpj_identifier(str(item))
            except ValueError as error:
                raise ValueError("Todos os CNPJs selecionados precisam ser válidos.") from error
            if normalized.isdigit() and not valid_cnpj(normalized):
                raise ValueError("Todos os CNPJs selecionados precisam ser válidos.")
            if normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned
