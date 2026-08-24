import base64
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = "https://plataforma-receita-matcher.ztnbow.easypanel.host"
ENV_FILE = Path("/srv/plataforma-receita/app.env")


def credentials() -> tuple[str, str]:
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values["APP_USERNAME"], values["APP_PASSWORD"]


def authorization() -> str:
    username, password = credentials()
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def main() -> None:
    try:
        urlopen(BASE_URL, timeout=10)
        unauthenticated_status = 200
    except HTTPError as error:
        unauthenticated_status = error.code

    index_request = Request(BASE_URL, headers={"Authorization": authorization()})
    with urlopen(index_request, timeout=10) as response:
        html = response.read().decode("utf-8")
        index_status = response.status

    body = json.dumps({
        "active_only": True,
        "check_website": False,
        "items": [{
            "local_id": "1",
            "name": "BrasilcomZ - Zootecnia Tropical",
            "address": "Avenida Pedro Marques",
            "municipality": "Jaboticabal",
            "uf": "SP",
            "postal_code": "14882-222",
            "website": "http://www.brasilcomz.com.br",
        }],
    }).encode("utf-8")
    match_request = Request(
        f"{BASE_URL}/api/matches/batch",
        data=body,
        method="POST",
        headers={"Authorization": authorization(), "Content-Type": "application/json"},
    )
    with urlopen(match_request, timeout=30) as response:
        payload = json.loads(response.read())
    result = payload["results"][0]

    jobs_request = Request(f"{BASE_URL}/api/jobs", headers={"Authorization": authorization()})
    with urlopen(jobs_request, timeout=10) as response:
        jobs_payload = json.loads(response.read())

    assert unauthenticated_status == 401
    assert index_status == 200
    assert "Encontrar o CNPJ correto" in html
    assert "Últimas consultas" in html
    assert "Baixar modelo de CSV" in html
    assert result["status"] == "confirmado"
    assert result["selected"]["cnpj"] == "12484145000194"
    assert isinstance(jobs_payload["jobs"], list)
    print(json.dumps({
        "public_authentication": "ok",
        "interface": "ok",
        "history_api": "ok",
        "status": result["status"],
        "cnpj": result["selected"]["cnpj"],
        "dataset_version": payload["dataset_version"],
        "timing_ms": payload["timing_ms"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
