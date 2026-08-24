import base64
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import BatchRequest
from .repository import Repository
from .website import WebsiteChecker


settings = get_settings()
repository = Repository(
    settings.postgres_dsn,
    database_workers=settings.database_workers,
    statement_timeout_ms=settings.database_statement_timeout_ms,
)
website_checker = WebsiteChecker(
    settings.website_cache_path,
    timeout=settings.website_timeout_seconds,
    workers=settings.website_workers,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.open()
    yield
    repository.close()
    website_checker.close()


app = FastAPI(title="Plataforma Receita - Matcher CNPJ", version="0.1.0", lifespan=lifespan)
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not settings.app_username or not settings.app_password:
        raise HTTPException(status_code=503, detail="autenticacao nao configurada")
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="autenticacao necessaria", headers={"WWW-Authenticate": "Basic"})
    try:
        decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(status_code=401, detail="credenciais invalidas", headers={"WWW-Authenticate": "Basic"})
    if not (hmac.compare_digest(username, settings.app_username) and hmac.compare_digest(password, settings.app_password)):
        raise HTTPException(status_code=401, detail="credenciais invalidas", headers={"WWW-Authenticate": "Basic"})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "dataset_version": repository.current_version()}


@app.get("/")
def index(_: None = Depends(require_auth)):
    return FileResponse(static_dir / "index.html")


@app.post("/api/matches/batch")
def matches(payload: BatchRequest, _: None = Depends(require_auth)) -> dict:
    if len(payload.items) > settings.max_batch_size:
        raise HTTPException(status_code=422, detail=f"maximo de {settings.max_batch_size} itens")
    started = monotonic()
    items = [item.model_dump() for item in payload.items]
    results, version, database_ms = repository.match_batch(items, payload.active_only)
    website_ms = 0
    website_evidence = {}
    if payload.check_website:
        unresolved_ids = {result["local_id"] for result in results if result["status"] != "confirmado"}
        unresolved = [item for item in items if item["local_id"] in unresolved_ids and item.get("website")]
        if unresolved:
            website_started = monotonic()
            website_evidence = website_checker.check_many(unresolved)
            website_ms = round((monotonic() - website_started) * 1000)
            refined = []
            for item in unresolved:
                evidence = website_evidence.get(item["local_id"], {})
                refined.append({
                    **item,
                    "site_cnpjs": evidence.get("cnpjs", []),
                    "site_names": evidence.get("names", []),
                })
            refined_results, _, refined_database_ms = repository.match_batch(refined, payload.active_only)
            database_ms += refined_database_ms
            refined_by_id = {result["local_id"]: result for result in refined_results}
            results = [refined_by_id.get(result["local_id"], result) for result in results]
    for result in results:
        result["website_evidence"] = website_evidence.get(result["local_id"])
    return {
        "results": results,
        "dataset_version": version,
        "timing_ms": {
            "database": database_ms,
            "website": website_ms,
            "total": round((monotonic() - started) * 1000),
        },
    }
