import base64
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from psycopg.errors import QueryCanceled

from .auth import AuthStore, LoginRateLimiter, normalize_identifier, token_digest
from .config import get_settings
from .jobs import JobRunner, JobStore, export_companies_csv
from .matching import MatchingService
from .models import (
    AccountUpdateRequest,
    BatchRequest,
    CompanyLookupRequest,
    CompanySearchRequest,
    JobRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    SignupRequest,
    SignupVerificationRequest,
    OrganizationRequest,
    MemberRoleRequest,
    InvitationRequest,
    InvitationTokenRequest,
    InvitationAcceptRequest,
    CompanyListRequest,
    CompanySelectionRequest,
    SavedSearchRequest,
    SavedSearchRunRequest,
    VALID_UFS,
)
from .repository import Repository
from .search import SearchCapabilityUnavailable
from .explorer import normalize_cnpj_identifier
from .website import WebsiteChecker
from .organizations import OrganizationError, invitation_hash
from .mail import mail_available, send_invitation, send_password_reset, send_signup_verification
from .saas import SaaSError, SaaSStore


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
auth_store = AuthStore(settings.auth_database_path, session_days=settings.auth_session_days)
auth_store.bootstrap(settings.app_username, settings.app_password)
legacy_organization_id = auth_store.ensure_initial_organization()
if legacy_organization_id:
    job_store.assign_legacy_organization(legacy_organization_id)
    if legacy_organization_id == settings.saas_internal_organization_id:
        auth_store.configure_internal_organization(
            legacy_organization_id,
            settings.saas_internal_organization_name,
        )
saas_store = SaaSStore(settings.saas_database_path)
if legacy_organization_id:
    saas_store.ensure_organization(
        legacy_organization_id,
        unlimited=legacy_organization_id == settings.saas_internal_organization_id,
        initial_credits=settings.saas_trial_credits,
    )
login_rate_limiter = LoginRateLimiter()
invitation_rate_limiter = LoginRateLimiter(attempts=20, window_seconds=3600)
password_reset_request_rate_limiter = LoginRateLimiter(attempts=5, window_seconds=3600)
password_reset_confirm_rate_limiter = LoginRateLimiter(attempts=10, window_seconds=15 * 60)
signup_rate_limiter = LoginRateLimiter(attempts=5, window_seconds=3600)
SESSION_COOKIE = "echopjs_session"


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.open()
    job_runner.start()
    yield
    job_runner.stop()
    repository.close()
    website_checker.close()
    job_store.close()
    saas_store.close()
    auth_store.close()


app = FastAPI(title="Plataforma Receita - Matcher CNPJ", version="0.1.0", lifespan=lifespan)
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.exception_handler(OrganizationError)
async def organization_error_handler(request: Request, error: OrganizationError):
    return JSONResponse({"detail": str(error)}, status_code=error.status)


@app.exception_handler(SaaSError)
async def saas_error_handler(request: Request, error: SaaSError):
    return JSONResponse({"detail": str(error)}, status_code=error.status)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError):
    # Do not echo submitted passwords, invitation tokens or arbitrary input in errors.
    return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()]}, status_code=422)


@app.middleware("http")
async def prevent_stale_application_state(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        allowed_origins = {settings.app_public_url.rstrip("/"), str(request.base_url).rstrip("/")}
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin.rstrip("/") not in allowed_origins):
            return JSONResponse({"detail": "Origem da solicitação não autorizada."}, status_code=403)
    response = await call_next(request)
    if request.url.path in {"/", "/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/account", "/organizations", "/invite"} or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        expires=expires_at,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def authenticate_request(request: Request) -> tuple[dict | None, str | None]:
    user = auth_store.user_for_session(request.cookies.get(SESSION_COOKIE))
    if user:
        return user, "session"
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.startswith("Basic "):
        return None, None
    try:
        decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode("utf-8")
        identifier, password = decoded.split(":", 1)
    except Exception:
        return None, None
    user = auth_store.authenticate(identifier, password)
    return (user, "basic") if user else (None, None)


def require_auth(request: Request) -> dict:
    user, _ = authenticate_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="sessao expirada ou acesso nao autorizado")
    return user


def require_organization(request: Request, user: dict = Depends(require_auth)) -> dict:
    raw = request.headers.get("X-Organization-Id") or request.query_params.get("organization_id")
    try:
        org_id = int(raw) if raw is not None else None
        if org_id is not None and org_id <= 0:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Organização inválida.")
    org = auth_store.organization_for_user(user["id"], org_id)
    billing = saas_store.ensure_organization(
        org["id"],
        unlimited=org["id"] == settings.saas_internal_organization_id,
        initial_credits=settings.saas_trial_credits,
    )
    return {
        **user,
        "organization_id": org["id"],
        "organization_role": org["role"],
        "billing": billing,
    }


def require_internal_organization(user: dict = Depends(require_organization)) -> dict:
    if not user["billing"]["is_internal"]:
        raise HTTPException(status_code=403, detail="Recurso disponível somente para a equipe interna.")
    return user


def page_response(request: Request, filename: str, *, next_path: str) -> Response:
    user, source = authenticate_request(request)
    if not user:
        target = next_path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=303)
    response = FileResponse(
        static_dir / filename,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )
    if source == "basic":
        token, expires_at = auth_store.create_session(user["id"])
        set_session_cookie(response, token, expires_at)
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "dataset_version": repository.current_version()}


@app.get("/")
def index(request: Request) -> Response:
    return page_response(request, "index.html", next_path="/")


@app.get("/login")
def login_page(request: Request) -> Response:
    user, _ = authenticate_request(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return FileResponse(static_dir / "login.html")


@app.get("/forgot-password")
def forgot_password_page() -> Response:
    return FileResponse(static_dir / "forgot-password.html")


@app.get("/reset-password")
def reset_password_page() -> Response:
    return FileResponse(static_dir / "reset-password.html")


@app.get("/signup")
def signup_page(request: Request) -> Response:
    user, _ = authenticate_request(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return FileResponse(static_dir / "signup.html")


@app.get("/verify-email")
def verify_email_page() -> Response:
    return FileResponse(static_dir / "verify-email.html")


@app.get("/account")
def account_page(request: Request) -> Response:
    return page_response(request, "account.html", next_path="/account")


@app.get("/organizations")
def organizations_page(request: Request) -> Response:
    return page_response(request, "organizations.html", next_path="/organizations")


@app.get("/invite")
def invite_page() -> Response:
    return FileResponse(static_dir / "invite.html")


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    user, _ = authenticate_request(request)
    return {
        "authenticated": bool(user),
        "identifier": user["identifier"] if user else None,
        "password_reset_available": mail_available(settings),
        "signup_available": settings.saas_self_signup_enabled and mail_available(settings),
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request) -> Response:
    client = request.client.host if request.client else "unknown"
    rate_key = f"{client}:{normalize_identifier(payload.identifier)}"
    now = monotonic()
    if not login_rate_limiter.allowed(rate_key, now):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos e tente novamente.")
    user = auth_store.authenticate(payload.identifier, payload.password)
    if not user:
        login_rate_limiter.failed(rate_key, now)
        raise HTTPException(status_code=401, detail="E-mail/usuario ou senha incorretos.")
    login_rate_limiter.succeeded(rate_key)
    token, expires_at = auth_store.create_session(user["id"])
    response = JSONResponse({"authenticated": True, "identifier": user["identifier"]})
    set_session_cookie(response, token, expires_at)
    return response


@app.post("/api/auth/password-reset/request", status_code=202)
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    client = request.client.host if request.client else "unknown"
    normalized = normalize_identifier(payload.identifier)
    now = monotonic()
    ip_key = f"password-reset-request:ip:{client}"
    account_key = f"password-reset-request:account:{normalized}"
    allowed = (
        password_reset_request_rate_limiter.allowed(ip_key, now)
        and password_reset_request_rate_limiter.allowed(account_key, now)
    )
    if allowed:
        password_reset_request_rate_limiter.failed(ip_key, now)
        password_reset_request_rate_limiter.failed(account_key, now)
        if mail_available(settings):
            reset = auth_store.create_password_reset(
                normalized,
                valid_minutes=settings.auth_password_reset_minutes,
            )
            if reset:
                link = f"{settings.app_public_url.rstrip('/')}/reset-password#token={reset['token']}"
                background_tasks.add_task(
                    send_password_reset,
                    settings,
                    email=reset["identifier"],
                    link=link,
                    valid_minutes=settings.auth_password_reset_minutes,
                )
    return {"accepted": True}


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirmRequest, request: Request) -> Response:
    client = request.client.host if request.client else "unknown"
    digest = token_digest(payload.token)
    now = monotonic()
    ip_key = f"password-reset-confirm:ip:{client}"
    token_key = f"password-reset-confirm:token:{digest}"
    if not (
        password_reset_confirm_rate_limiter.allowed(ip_key, now)
        and password_reset_confirm_rate_limiter.allowed(token_key, now)
    ):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Solicite um novo link e tente novamente.")
    updated = auth_store.reset_password(payload.token, payload.new_password)
    if not updated:
        password_reset_confirm_rate_limiter.failed(ip_key, now)
        password_reset_confirm_rate_limiter.failed(token_key, now)
        raise HTTPException(status_code=404, detail="Este link é inválido, expirou ou já foi utilizado.")
    password_reset_confirm_rate_limiter.succeeded(token_key)
    token, expires_at = auth_store.create_session(updated["id"])
    response = JSONResponse({"reset": True, "identifier": updated["identifier"]})
    set_session_cookie(response, token, expires_at)
    return response


@app.post("/api/auth/signup", status_code=202)
def signup(payload: SignupRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    if not settings.saas_self_signup_enabled or not mail_available(settings):
        raise HTTPException(status_code=503, detail="O cadastro público ainda não está disponível.")
    client = request.client.host if request.client else "unknown"
    now = monotonic()
    ip_key = f"signup:ip:{client}"
    email_key = f"signup:email:{payload.email}"
    if not (signup_rate_limiter.allowed(ip_key, now) and signup_rate_limiter.allowed(email_key, now)):
        raise HTTPException(status_code=429, detail="Muitas tentativas de cadastro. Aguarde e tente novamente.")
    signup_rate_limiter.failed(ip_key, now)
    signup_rate_limiter.failed(email_key, now)
    pending = auth_store.create_signup(
        payload.email,
        payload.password,
        payload.name,
        valid_hours=settings.auth_signup_verification_hours,
    )
    link = f"{settings.app_public_url.rstrip('/')}/verify-email#token={pending['token']}"
    background_tasks.add_task(
        send_signup_verification,
        settings,
        email=pending["identifier"],
        link=link,
        valid_hours=settings.auth_signup_verification_hours,
    )
    return {"accepted": True, "email": pending["identifier"]}


@app.post("/api/auth/signup/verify")
def verify_signup(payload: SignupVerificationRequest) -> Response:
    completed = auth_store.complete_signup(payload.token)
    if not completed:
        raise HTTPException(status_code=404, detail="Este link é inválido, expirou ou já foi utilizado.")
    user, organization_id = completed
    saas_store.ensure_organization(
        organization_id,
        initial_credits=settings.saas_trial_credits,
    )
    token, expires_at = auth_store.create_session(user["id"])
    response = JSONResponse({"verified": True, "organization_id": organization_id})
    set_session_cookie(response, token, expires_at)
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> Response:
    auth_store.delete_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return response


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(require_auth)) -> dict:
    return {"id": user["id"], "identifier": user["identifier"], "created_at": user["created_at"]}


@app.put("/api/auth/account")
def update_account(payload: AccountUpdateRequest, request: Request, user: dict = Depends(require_auth)) -> Response:
    try:
        updated = auth_store.update_account(
            user["id"],
            current_password=payload.current_password,
            identifier=payload.identifier,
            new_password=payload.new_password,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Este e-mail não está disponível. Escolha outro.")
    if not updated:
        raise HTTPException(status_code=401, detail="A senha atual esta incorreta.")
    token, expires_at = auth_store.create_session(updated["id"])
    response = JSONResponse({"updated": True, "identifier": updated["identifier"]})
    set_session_cookie(response, token, expires_at)
    return response


@app.get("/api/organizations")
def list_organizations(user: dict = Depends(require_auth)) -> dict:
    organizations = auth_store.organizations_for_user(user["id"])
    for organization in organizations:
        organization["billing"] = saas_store.ensure_organization(
            organization["id"],
            unlimited=organization["id"] == settings.saas_internal_organization_id,
            initial_credits=settings.saas_trial_credits,
        )
    return {"user": user, "organizations": organizations, "can_create": any(o["role"] == "admin" for o in organizations), "email_delivery_available": mail_available(settings)}


@app.post("/api/organizations", status_code=201)
def create_organization(payload: OrganizationRequest, user: dict = Depends(require_auth)) -> dict:
    key = f"org-create:{user['id']}"
    if not invitation_rate_limiter.allowed(key, monotonic()):
        raise HTTPException(status_code=429, detail="Limite temporário de criação atingido. Tente mais tarde.")
    org = auth_store.create_organization(user["id"], payload.name)
    org["billing"] = saas_store.ensure_organization(
        org["id"], initial_credits=settings.saas_trial_credits
    )
    invitation_rate_limiter.failed(key, monotonic())
    return org


@app.get("/api/organizations/{org_id}")
def organization_team(org_id: int, user: dict = Depends(require_auth)) -> dict:
    return auth_store.organization_team(user["id"], org_id)


@app.put("/api/organizations/{org_id}")
def rename_organization(org_id: int, payload: OrganizationRequest, user: dict = Depends(require_auth)) -> dict:
    return auth_store.rename_organization(user["id"], org_id, payload.name)


@app.put("/api/organizations/{org_id}/members/{member_id}")
def change_member_role(org_id: int, member_id: int, payload: MemberRoleRequest, user: dict = Depends(require_auth)) -> dict:
    auth_store.change_member(user["id"], org_id, member_id, payload.role)
    return {"updated": True}


@app.delete("/api/organizations/{org_id}/members/{member_id}")
def remove_member(org_id: int, member_id: int, user: dict = Depends(require_auth)) -> dict:
    auth_store.change_member(user["id"], org_id, member_id)
    return {"removed": True}


@app.post("/api/organizations/{org_id}/invitations", status_code=201)
def create_invitation(org_id: int, payload: InvitationRequest, user: dict = Depends(require_auth)) -> dict:
    key = f"invite-create:{user['id']}"
    if not invitation_rate_limiter.allowed(key, monotonic()):
        raise HTTPException(status_code=429, detail="Limite temporário de convites atingido. Tente mais tarde.")
    invite = auth_store.create_invitation(user["id"], org_id, payload.email, payload.role)
    invitation_rate_limiter.failed(key, monotonic())
    link = f"{settings.app_public_url.rstrip('/')}/invite#token={invite.pop('token')}"
    delivery = send_invitation(settings, email=invite["email"], organization_name=invite["organization_name"], link=link) if payload.send_email else "manual"
    return {**invite, "link": link, "delivery": delivery}


@app.delete("/api/organizations/{org_id}/invitations/{invitation_id}")
def revoke_invitation(org_id: int, invitation_id: int, user: dict = Depends(require_auth)) -> dict:
    auth_store.revoke_invitation(user["id"], org_id, invitation_id)
    return {"revoked": True}


def invitation_attempt(request: Request, token: str):
    client = request.client.host if request.client else "unknown"
    key = f"invite-accept:{client}:{invitation_hash(token)}"
    if not login_rate_limiter.allowed(key, monotonic()):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.")
    return key


@app.post("/api/invitations/preview")
def invitation_preview(payload: InvitationTokenRequest, request: Request) -> dict:
    key = invitation_attempt(request, payload.token)
    try:
        return auth_store.invitation_preview(payload.token)
    except OrganizationError:
        login_rate_limiter.failed(key, monotonic())
        raise


@app.post("/api/invitations/accept")
def accept_invitation(payload: InvitationAcceptRequest, request: Request) -> Response:
    key = invitation_attempt(request, payload.token)
    try:
        user, org_id = auth_store.accept_invitation(payload.token, payload.password)
    except OrganizationError:
        login_rate_limiter.failed(key, monotonic())
        raise
    login_rate_limiter.succeeded(key)
    auth_store.delete_session(request.cookies.get(SESSION_COOKIE))
    token, expires = auth_store.create_session(user["id"])
    response = JSONResponse({"accepted": True, "organization_id": org_id})
    set_session_cookie(response, token, expires)
    return response


@app.get("/api/billing/summary")
def billing_summary(user: dict = Depends(require_organization)) -> dict:
    return saas_store.billing_summary(user["organization_id"])


@app.get("/api/dashboard")
def dashboard(user: dict = Depends(require_organization)) -> dict:
    summary = saas_store.dashboard_summary(user["organization_id"])
    jobs = job_store.list_jobs(limit=5, organization_id=user["organization_id"])
    summary["recent_jobs"] = jobs
    summary["active_jobs"] = job_store.active_job_count(organization_id=user["organization_id"])
    return summary


@app.post("/api/credits/estimate")
def estimate_credits(payload: CompanySelectionRequest, user: dict = Depends(require_organization)) -> dict:
    return saas_store.credit_estimate(user["organization_id"], payload.cnpjs)


@app.get("/api/saved-searches")
def list_saved_searches(user: dict = Depends(require_organization)) -> dict:
    return {"saved_searches": saas_store.list_saved_searches(user["organization_id"])}


@app.post("/api/saved-searches", status_code=201)
def create_saved_search(payload: SavedSearchRequest, user: dict = Depends(require_organization)) -> dict:
    return saas_store.create_saved_search(
        user["organization_id"],
        user["id"],
        name=payload.name,
        filters=payload.filters.model_dump(mode="json"),
        result_count=payload.result_count,
    )


@app.get("/api/saved-searches/{search_id}")
def get_saved_search(search_id: str, user: dict = Depends(require_organization)) -> dict:
    saved = saas_store.saved_search(user["organization_id"], search_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Busca salva não encontrada.")
    return saved


@app.post("/api/saved-searches/{search_id}/runs")
def record_saved_search_run(
    search_id: str,
    payload: SavedSearchRunRequest,
    user: dict = Depends(require_organization),
) -> dict:
    return saas_store.record_saved_search_run(
        user["organization_id"], search_id, payload.result_count
    )


@app.delete("/api/saved-searches/{search_id}")
def delete_saved_search(search_id: str, user: dict = Depends(require_organization)) -> dict:
    if not saas_store.delete_saved_search(user["organization_id"], search_id):
        raise HTTPException(status_code=404, detail="Busca salva não encontrada.")
    return {"deleted": True}


@app.get("/api/company-lists")
def list_company_lists(user: dict = Depends(require_organization)) -> dict:
    return {"lists": saas_store.list_company_lists(user["organization_id"])}


@app.post("/api/company-lists", status_code=201)
def create_company_list(payload: CompanyListRequest, user: dict = Depends(require_organization)) -> dict:
    return saas_store.create_company_list(
        user["organization_id"],
        user["id"],
        name=payload.name,
        description=payload.description,
    )


@app.get("/api/company-lists/{list_id}")
def get_company_list(list_id: str, user: dict = Depends(require_organization)) -> dict:
    company_list = saas_store.company_list_detail(user["organization_id"], list_id)
    if not company_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada.")
    return company_list


@app.post("/api/company-lists/{list_id}/companies")
def add_companies_to_list(
    list_id: str,
    payload: CompanySelectionRequest,
    user: dict = Depends(require_organization),
) -> dict:
    found = repository.companies_by_cnpjs(payload.cnpjs)
    companies = [found[cnpj] for cnpj in payload.cnpjs if cnpj in found]
    if len(companies) != len(payload.cnpjs):
        raise HTTPException(status_code=409, detail="Uma ou mais empresas não estão disponíveis na versão atual da Receita.")
    return saas_store.add_companies(
        user["organization_id"], list_id, user["id"], companies
    )


@app.post("/api/exports/companies")
def export_companies(payload: CompanySelectionRequest, user: dict = Depends(require_organization)) -> Response:
    found = repository.companies_by_cnpjs(payload.cnpjs)
    companies = [found[cnpj] for cnpj in payload.cnpjs if cnpj in found]
    if len(companies) != len(payload.cnpjs):
        raise HTTPException(status_code=409, detail="Uma ou mais empresas não estão disponíveis na versão atual da Receita.")
    content = export_companies_csv(companies)
    unlock = saas_store.unlock_companies(
        user["organization_id"], user["id"], [company["cnpj"] for company in companies],
        kind="company_export", description=f"Exportação de {len(companies)} empresa(s)",
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="empresas-echopjs.csv"',
            "X-Credits-Spent": str(unlock["credits_spent"]),
            "X-Credit-Balance": str(unlock["credit_balance"]),
            "X-Unlimited-Credits": str(unlock["unlimited_credits"]).lower(),
        },
    )


@app.delete("/api/company-lists/{list_id}/companies/{cnpj}")
def remove_company_from_list(
    list_id: str,
    cnpj: str,
    user: dict = Depends(require_organization),
) -> dict:
    normalized = normalize_cnpj_identifier(cnpj)
    if not saas_store.remove_company(user["organization_id"], list_id, normalized):
        raise HTTPException(status_code=404, detail="Empresa não encontrada nesta lista.")
    return {"removed": True}


@app.delete("/api/company-lists/{list_id}")
def delete_company_list(list_id: str, user: dict = Depends(require_organization)) -> dict:
    if not saas_store.delete_company_list(user["organization_id"], list_id):
        raise HTTPException(status_code=404, detail="Lista não encontrada.")
    return {"deleted": True}


@app.post("/api/matches/batch")
def matches(payload: BatchRequest, _: dict = Depends(require_organization)) -> dict:
    if len(payload.items) > settings.max_batch_size:
        raise HTTPException(status_code=422, detail=f"maximo de {settings.max_batch_size} itens")
    items = [item.model_dump() for item in payload.items]
    return matching_service.match_items(
        items,
        active_only=payload.active_only,
        check_website=payload.check_website,
    )


@app.post("/api/jobs")
def create_job(payload: JobRequest, user: dict = Depends(require_organization)) -> dict:
    if len(payload.items) > settings.max_job_size:
        raise HTTPException(status_code=422, detail=f"maximo de {settings.max_job_size} itens")
    items = [item.model_dump() for item in payload.items]
    job = job_store.create_job(
        payload.filename,
        items,
        active_only=payload.active_only,
        check_website=payload.check_website,
        organization_id=user["organization_id"],
        created_by=user["id"],
    )
    job_runner.notify()
    return job


@app.get("/api/jobs")
def list_jobs(user: dict = Depends(require_organization)) -> dict:
    return {"jobs": job_store.list_jobs(organization_id=user["organization_id"])}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: dict = Depends(require_organization)) -> dict:
    job = job_store.get_job(job_id, organization_id=user["organization_id"])
    if not job:
        raise HTTPException(status_code=404, detail="consulta nao encontrada")
    return job


@app.get("/api/jobs/{job_id}/export.csv")
def export_job(job_id: str, user: dict = Depends(require_organization)) -> Response:
    selected_cnpjs = job_store.selected_cnpjs(job_id, organization_id=user["organization_id"])
    if selected_cnpjs is None:
        raise HTTPException(status_code=404, detail="consulta nao encontrada")
    content = job_store.export_csv(job_id, repository.companies_by_cnpjs, organization_id=user["organization_id"])
    if content is None:
        raise HTTPException(status_code=404, detail="consulta nao encontrada")
    unlock = saas_store.unlock_companies(
        user["organization_id"], user["id"], selected_cnpjs,
        kind="job_export", description=f"Exportação do processamento {job_id}", reference_id=job_id,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="resultado-{job_id}.csv"',
            "X-Credits-Spent": str(unlock["credits_spent"]),
            "X-Credit-Balance": str(unlock["credit_balance"]),
            "X-Unlimited-Credits": str(unlock["unlimited_credits"]).lower(),
        },
    )


@app.get("/api/jobs/{job_id}/credit-estimate")
def estimate_job_export(job_id: str, user: dict = Depends(require_organization)) -> dict:
    selected_cnpjs = job_store.selected_cnpjs(job_id, organization_id=user["organization_id"])
    if selected_cnpjs is None:
        raise HTTPException(status_code=404, detail="consulta nao encontrada")
    return saas_store.credit_estimate(user["organization_id"], selected_cnpjs)


@app.get("/api/search/capabilities")
def search_capabilities(_: dict = Depends(require_organization)) -> dict:
    return {
        "dataset_version": repository.current_version(),
        "filters": repository.search_capabilities().as_dict(),
        "max_results": 10000,
    }


@app.get("/api/search/options/cnaes")
def search_cnae_options(_: dict = Depends(require_organization)) -> dict:
    return {
        "dataset_version": repository.current_version(),
        "options": repository.search_cnae_options(),
    }


@app.get("/api/search/options/municipalities")
def search_municipality_options(
    uf: str | None = Query(default=None, min_length=2, max_length=2),
    ufs: list[str] = Query(default=[]),
    _: dict = Depends(require_organization),
) -> dict:
    normalized_ufs = list(dict.fromkeys(
        value.strip().upper() for value in [*ufs, *([uf] if uf else [])] if value.strip()
    ))
    if not normalized_ufs or set(normalized_ufs) - VALID_UFS:
        raise HTTPException(status_code=422, detail="UF invalida")
    return {
        "dataset_version": repository.current_version(),
        "ufs": normalized_ufs,
        "options": repository.search_municipality_options(normalized_ufs),
    }


@app.post("/api/search")
def search_companies(payload: CompanySearchRequest, _: dict = Depends(require_organization)) -> dict:
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


@app.get("/api/explorer/overview")
def explorer_overview(_: dict = Depends(require_organization)) -> dict:
    return repository.explorer_overview()


@app.get("/api/explorer/schema")
def explorer_schema(_: dict = Depends(require_internal_organization)) -> dict:
    return repository.database_schema()


@app.get("/api/explorer/relations/{relation_name}/preview")
def explorer_relation_preview(
    relation_name: str,
    limit: int = Query(default=10, ge=1, le=20),
    _: dict = Depends(require_internal_organization),
) -> dict:
    try:
        return repository.preview_relation(relation_name, limit=limit)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="tabela ou visao nao disponivel para pre-visualizacao") from error
    except QueryCanceled as error:
        raise HTTPException(status_code=408, detail="a pre-visualizacao excedeu o limite seguro de tempo") from error


def company_lookup_results(cnpjs: list[str]) -> dict:
    started = monotonic()
    entries: list[tuple[str, str | None]] = []
    normalized_cnpjs: list[str] = []
    for original in cnpjs:
        try:
            normalized = normalize_cnpj_identifier(original)
        except ValueError:
            entries.append((original, None))
            continue
        entries.append((original, normalized))
        if normalized not in normalized_cnpjs:
            normalized_cnpjs.append(normalized)
    found = repository.companies_by_cnpjs(normalized_cnpjs)
    results = []
    for original, normalized in entries:
        company = found.get(normalized) if normalized else None
        results.append({
            "input": original,
            "normalized_cnpj": normalized,
            "status": "found" if company else "not_found" if normalized else "invalid",
            "company": company,
        })
    return {
        "results": results,
        "total": len(results),
        "found": sum(item["status"] == "found" for item in results),
        "not_found": sum(item["status"] == "not_found" for item in results),
        "invalid": sum(item["status"] == "invalid" for item in results),
        "dataset_version": repository.current_version(),
        "timing_ms": round((monotonic() - started) * 1000),
    }


@app.post("/api/explorer/company-lookup")
def explorer_company_lookup(payload: CompanyLookupRequest, _: dict = Depends(require_organization)) -> dict:
    return company_lookup_results(payload.cnpjs)


@app.post("/api/exports/cnpj-lookup")
def export_cnpj_lookup(payload: CompanyLookupRequest, user: dict = Depends(require_organization)) -> Response:
    lookup = company_lookup_results(payload.cnpjs)
    found_cnpjs = [item["company"]["cnpj"] for item in lookup["results"] if item["company"]]
    companies = [item["company"] or {} for item in lookup["results"]]
    leading_rows = [[item["input"], item["status"]] for item in lookup["results"]]
    content = export_companies_csv(
        companies,
        leading_headers=["CNPJ informado", "Resultado"],
        leading_rows=leading_rows,
    )
    unlock = saas_store.unlock_companies(
        user["organization_id"], user["id"], found_cnpjs,
        kind="cnpj_export", description=f"Exportação de {len(found_cnpjs)} CNPJ(s)",
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="consulta-cnpjs-echopjs.csv"',
            "X-Credits-Spent": str(unlock["credits_spent"]),
            "X-Credit-Balance": str(unlock["credit_balance"]),
            "X-Unlimited-Credits": str(unlock["unlimited_credits"]).lower(),
        },
    )


@app.get("/api/explorer/companies/{cnpj}")
def explorer_company(cnpj: str, _: dict = Depends(require_organization)) -> dict:
    try:
        normalized = normalize_cnpj_identifier(cnpj)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    company = repository.company_detail(normalized)
    if not company:
        raise HTTPException(status_code=404, detail="CNPJ nao encontrado na base da Receita")
    return company


@app.get("/api/explorer/companies/{cnpj}/establishments")
def explorer_company_establishments(cnpj: str, _: dict = Depends(require_organization)) -> dict:
    try:
        normalized = normalize_cnpj_identifier(cnpj)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    group = repository.company_establishments(normalized)
    if not group:
        raise HTTPException(status_code=404, detail="CNPJ nao encontrado na base da Receita")
    return group
