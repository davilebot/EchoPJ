import base64
import json
import os
import time
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:8000"


def auth_header() -> str:
    value = f"{os.environ['APP_USERNAME']}:{os.environ['APP_PASSWORD']}"
    return "Basic " + base64.b64encode(value.encode()).decode()


def request(path: str, *, data: dict | None = None) -> tuple[bytes, dict]:
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Authorization": auth_header()}
    if body:
        headers["Content-Type"] = "application/json"
    req = Request(BASE_URL + path, data=body, method="POST" if body else "GET", headers=headers)
    with urlopen(req, timeout=30) as response:
        return response.read(), dict(response.headers)


def main() -> None:
    row = {
        "name": "BrasilcomZ - Zootecnia Tropical",
        "address": "Avenida Pedro Marques",
        "municipality": "Jaboticabal",
        "uf": "SP",
        "postal_code": "14882-222",
        "website": "http://www.brasilcomz.com.br",
    }
    items = [
        {**row, "local_id": str(position + 2), "source": {"Company Name": row["name"], "Ordem": str(position + 1)}}
        for position in range(3)
    ]
    raw, _ = request("/api/jobs", data={
        "filename": "smoke-jobs.csv",
        "items": items,
        "active_only": True,
        "check_website": False,
    })
    job = json.loads(raw)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        raw, _ = request(f"/api/jobs/{job['id']}")
        job = json.loads(raw)
        if job["status"] in {"completed", "completed_with_errors"}:
            break
        time.sleep(0.2)

    raw, _ = request("/api/jobs")
    history = json.loads(raw)["jobs"]
    exported, headers = request(f"/api/jobs/{job['id']}/export.csv")
    text = exported.decode("utf-8-sig")
    disposition = next((value for key, value in headers.items() if key.casefold() == "content-disposition"), "")

    assert job["status"] == "completed"
    assert job["processed"] == 3
    assert job["confirmed"] == 3
    assert history[0]["id"] == job["id"]
    assert "Company Name" in text and "12484145000194" in text
    assert "attachment" in disposition
    print(json.dumps({
        "job_status": job["status"],
        "processed": job["processed"],
        "confirmed": job["confirmed"],
        "history": "ok",
        "export": "ok",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
