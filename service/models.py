from pydantic import BaseModel, Field, field_validator

from plataforma_receita.normalization import digits, valid_cnpj


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
