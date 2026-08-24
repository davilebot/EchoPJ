import base64
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.errors import QueryCanceled

from .config import get_settings
from .jobs import JobRunner, JobStore
from .matching import MatchingService
from .models import BatchRequest, CompanySearchRequest, JobRequest
from .repository import Repository
from .search import SearchCapabilityUnavailable
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
matching_service = MatchingService(repository, website_checker)
job_store = JobStore(settings.job_database_path)
job_runner = JobRunner(job_store, matching_service.match_items)


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.open()
    job_runner.start()
    yield
    job_runner.stop()
    repository.close()
    website_checker.close()
    job_store.close()


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
    items = [item.model_dump() for item in payload.items]
    return matching_service.match_items(
        items,
        active_only=payload.active_only,
        check_website=payload.check_website,
    )


@app.post("/api/jobs")
def create_job(payload: JobRequest, _: None = Depends(require_auth)) -> dict:
    if len(payload.items) > settings.max_job_size:
        raise HTTPException(status_code=422, detail=f"maximo de {settings.max_job_size} itens")
    items = [item.model_dump() for item in payload.items]
    job = job_store.create_job(
        payload.filename,
        items,
        active_only=payload.active_only,
        check_website=payload.check_website,
    )
    job_runner.notify()
    return job


@app.get("/api/jobs")
def list_jobs(_: None = Depends(require_auth)) -> dict:
    return {"jobs": job_store.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(require_auth)) -> dict:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="consulta nao encontrada")
    return job


@app.get("/api/jobs/{job_id}/export.csv")
def export_job(job_id: str, _: None = Depends(require_auth)) -> Response:
    content = job_store.export_csv(job_id)
    if content is None:
        raise HTTPException(status_code=404, detail="consulta nao encontrada")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="resultado-{job_id}.csv"'},
    )


@app.get("/api/search/capabilities")
def search_capabilities(_: None = Depends(require_auth)) -> dict:
    return {
        "dataset_version": repository.current_version(),
        "filters": repository.search_capabilities().as_dict(),
        "max_results": 10000,
    }


@app.post("/api/search")
def search_companies(payload: CompanySearchRequest, _: None = Depends(require_auth)) -> dict:
    try:
        results, capabilities, duration_ms, has_more = repository.search_companies(payload.model_dump())
    except SearchCapabilityUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except QueryCanceled as error:
        raise HTTPException(
            status_code=408,
            detail="A busca ficou ampla demais. Acrescente uma regiao, UF ou CNAE e tente novamente.",
        ) from error
    return {
        "results": results,
        "returned": len(results),
        "limit": payload.limit,
        "has_more": has_more,
        "dataset_version": repository.current_version(),
        "capabilities": capabilities.as_dict(),
        "timing_ms": duration_ms,
    }
