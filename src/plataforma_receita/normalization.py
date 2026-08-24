import re
import unicodedata
from difflib import SequenceMatcher


LEGAL_WORDS = {
    "LTDA", "LIMITADA", "SA", "S", "A", "EIRELI", "ME", "EPP", "SPE",
    "SOCIEDADE", "EMPRESA", "BRASIL", "BRASILEIRA", "DO", "DA", "DOS", "DAS",
    "DE", "E", "COMERCIO", "SERVICOS", "SERVICO",
}
STREET_WORDS = {
    "RUA", "R", "AV", "AVENIDA", "ROD", "RODOVIA", "ESTRADA", "ALAMEDA",
    "TRAVESSA", "PRACA", "PC", "LOJA", "SALA", "ANDAR", "BLOCO",
}


def normalize(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in raw if not unicodedata.combining(char)).upper()
    return re.sub(r"[^A-Z0-9]+", " ", plain).strip()


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def tokens(value: str | None, ignored: set[str] | None = None) -> set[str]:
    ignored = ignored or set()
    return {token for token in normalize(value).split() if len(token) > 1 and token not in ignored}


def similarity(left: str | None, right: str | None, ignored: set[str] | None = None) -> float:
    a = tokens(left, ignored)
    b = tokens(right, ignored)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    jaccard = intersection / union
    containment = intersection / min(len(a), len(b))
    if intersection == 1 and min(len(a), len(b)) == 1:
        matched_token = next(iter(a & b))
        containment *= 0.9 if len(matched_token) >= 7 else 0.6
    sequence = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    if intersection == 0:
        sequence = min(sequence, 0.45)
    return max(jaccard, 0.9 * containment, sequence)


def address_number(value: str | None) -> str | None:
    numbers = re.findall(r"\b\d+[A-Z]?\b", normalize(value))
    return numbers[-1] if numbers else None


def valid_cnpj(value: str | None) -> bool:
    number = digits(value)
    if len(number) != 14 or len(set(number)) == 1:
        return False

    def digit(base: str, weights: list[int]) -> int:
        remainder = sum(int(char) * weight for char, weight in zip(base, weights)) % 11
        return 0 if remainder < 2 else 11 - remainder

    first = digit(number[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(number[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return number[-2:] == f"{first}{second}"
