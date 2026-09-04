import base64
import json
from pathlib import Path
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
    with urlopen(BASE_URL, timeout=10) as response:
        unauthenticated_status = response.status
        unauthenticated_url = response.geturl()
        login_html = response.read().decode("utf-8")

    index_request = Request(BASE_URL, headers={"Authorization": authorization()})
    with urlopen(index_request, timeout=10) as response:
        html = response.read().decode("utf-8")
        index_status = response.status

    static_assets = {}
    for path in (
        "/static/styles.css?v=20260902-auth-1",
        "/static/app.js?v=20260902-auth-1",
        "/static/auth.css?v=20260902-1",
        "/static/login.js?v=20260902-1",
        "/static/account.js?v=20260902-1",
        "/static/assets/echo-wordmark-dark.png",
        "/static/assets/echo-wordmark-light.png",
    ):
        asset_request = Request(f"{BASE_URL}{path}", headers={"Authorization": authorization()})
        with urlopen(asset_request, timeout=10) as response:
            content = response.read()
            assert response.status == 200
            assert len(content) > 100
            static_assets[path] = len(content)

    company_request = Request(
        f"{BASE_URL}/api/explorer/companies/12484145000194",
        headers={"Authorization": authorization()},
    )
    with urlopen(company_request, timeout=10) as response:
        company_payload = json.loads(response.read())
    assert company_payload["core"]["cnpj"] == "12484145000194"

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

    assert unauthenticated_status == 200
    assert unauthenticated_url.endswith("/login?next=/")
    assert "Entre no seu workspace" in login_html
    assert index_status == 200
    assert "EchoPJs" in html
    assert "Enriquecimento de CNPJ" in html
    assert "Consultar CNPJ" in html
    assert "Processamentos" in html
    assert "Baixar modelo" in html
    assert result["status"] == "confirmado"
    assert result["selected"]["cnpj"] == "12484145000194"
    assert isinstance(jobs_payload["jobs"], list)
    print(json.dumps({
        "public_authentication": "ok",
        "login_page": "ok",
        "interface": "ok",
        "static_assets": static_assets,
        "cnpj_lookup": "ok",
        "history_api": "ok",
        "status": result["status"],
        "cnpj": result["selected"]["cnpj"],
        "dataset_version": payload["dataset_version"],
        "timing_ms": payload["timing_ms"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
