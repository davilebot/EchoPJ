from fastapi.testclient import TestClient

from service.main import app


def main() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        unauthenticated = client.get("/")
        index = client.get("/", auth=("teste", "teste-local"))
        response = client.post(
            "/api/matches/batch",
            auth=("teste", "teste-local"),
            json={
                "active_only": True,
                "check_website": False,
                "items": [
                    {
                        "local_id": "1",
                        "name": "BrasilcomZ - Zootecnia Tropical",
                        "address": "Avenida Pedro Marques",
                        "municipality": "Jaboticabal",
                        "uf": "SP",
                        "postal_code": "14882-222",
                        "website": "http://www.brasilcomz.com.br",
                    }
                ],
            },
        )
        payload = response.json()
        result = payload["results"][0]
        assert health.status_code == 200
        assert unauthenticated.status_code == 401
        assert index.status_code == 200
        assert response.status_code == 200
        print({
            "health": health.json()["status"],
            "authentication": "ok",
            "status": result["status"],
            "cnpj": (result.get("selected") or {}).get("cnpj"),
            "dataset_version": payload["dataset_version"],
            "timing_ms": payload["timing_ms"],
        })


if __name__ == "__main__":
    main()
