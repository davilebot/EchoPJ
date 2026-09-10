import base64
import hashlib
import hmac
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
from .jobs import JobCapacityError, JobRunner, JobStore, export_companies_csv
from .limits import SlidingWindowRateLimiter
from .matching import MatchingService
from .models import (
    AccountUpdateRequest,
    BatchRequest,
    BillingCatalogDraftRequest,
    CompanyLookupRequest,
    CustomerWorkspaceRequest,
    CompanySearchRequest,
    JobRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PrivacyAdminUpdateRequest,
    PrivacyDeletionRequest,
    PrivacyPasswordRequest,
    SignupRequest,
    SignupVerificationRequest,
    BillingCancellationRequest,
    BillingCheckoutRequest,
    BillingProfileUpdateRequest,
    CreditAdjustmentRequest,
    SupportMessageRequest,
    SupportTicketAdminUpdateRequest,
    SupportTicketRequest,
    OrganizationRequest,
    MemberRoleRequest,
    InvitationRequest,
    InvitationDeliveryRequest,
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
from .billing_notifications import BillingEmailDispatcher
from .saas import SaaSError, SaaSStore
from .payments import AsaasClient, BillingCatalog, PaymentError, normalize_asaas_event
from .legal import LegalDocuments
from .launch import commercial_launch_readiness
from .operations import OperationsMonitor, backup_status


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
billing_catalog = BillingCatalog(settings.saas_billing_catalog_json)
legal_documents = LegalDocuments.from_settings(settings)
asaas_client = AsaasClient(
    settings.asaas_api_url,
    settings.asaas_api_key,
    timeout=settings.asaas_timeout_seconds,
)
billing_email_dispatcher = BillingEmailDispatcher(
    settings,
    saas_store,
    auth_store,
    enabled=settings.saas_billing_email_notifications_enabled and mail_available(settings),
    interval_seconds=settings.saas_billing_email_interval_minutes * 60,
)


def current_billing_catalog() -> BillingCatalog:
    published = saas_store.published_billing_catalog_json()
    return BillingCatalog(published) if published is not None else billing_catalog


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
heavy_rate_limiter = SlidingWindowRateLimiter(
    requests=settings.saas_heavy_requests_per_minute,
    window_seconds=60,
)
operations_monitor = OperationsMonitor()
SESSION_COOKIE = "echopjs_session"


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.open()
    job_runner.start()
    billing_email_dispatcher.start()
    yield
    billing_email_dispatcher.stop()
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


@app.exception_handler(PaymentError)
async def payment_error_handler(request: Request, error: PaymentError):
    return JSONResponse({"detail": str(error)}, status_code=error.status)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError):
    # Do not echo submitted passwords, invitation tokens or arbitrary input in errors.
    return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()]}, status_code=422)


@app.middleware("http")
async def prevent_stale_application_state(request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > settings.saas_max_request_bytes:
            return JSONResponse(
                {"detail": "O arquivo ou conjunto enviado excede o limite seguro desta operação."},
                status_code=413,
            )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        allowed_origins = {settings.app_public_url.rstrip("/"), str(request.base_url).rstrip("/")}
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin.rstrip("/") not in allowed_origins):
            return JSONResponse({"detail": "Origem da solicitação não autorizada."}, status_code=403)
    response = await call_next(request)
    if request.url.path in {"/", "/produto", "/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/account", "/organizations", "/invite", "/admin", "/help", "/plans", "/termos", "/privacidade", "/billing/return"} or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started = monotonic()
    status_code = 500
    operations_monitor.begin()
    request.state.request_id = request_id
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{(monotonic() - started) * 1000:.1f}"
        return response
    finally:
        route = request.scope.get("route")
        route_name = getattr(route, "path", "<unmatched>")
        operations_monitor.finish(
            route_name, request.method, status_code, (monotonic() - started) * 1000,
        )


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
    return resolve_organization_context(request, user)


def resolve_organization_context(request: Request, user: dict, *, allow_suspended: bool = False) -> dict:
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
    if billing["subscription_status"] == "suspended" and not allow_suspended:
        raise HTTPException(
            status_code=403,
            detail="O acesso desta organização está suspenso. Fale com o suporte da EchoHub.",
        )
    return {
        **user,
        "organization_id": org["id"],
        "organization_role": org["role"],
        "organization_permissions": org["permissions"],
        "billing": billing,
    }


def require_support_organization(request: Request, user: dict = Depends(require_auth)) -> dict:
    return resolve_organization_context(request, user, allow_suspended=True)


def require_internal_organization(user: dict = Depends(require_organization)) -> dict:
    if not user["billing"]["is_internal"]:
        raise HTTPException(status_code=403, detail="Recurso disponível somente para a equipe interna.")
    return user


def require_internal_admin(user: dict = Depends(require_organization)) -> dict:
    if not user["billing"]["is_internal"] or user["organization_role"] != "admin":
        raise HTTPException(status_code=403, detail="Acesso disponível somente para administradores da EchoHub.")
    return user


def enforce_heavy_rate_limit(user: dict, operation: str) -> None:
    decision = heavy_rate_limiter.consume(f"{user['organization_id']}:{operation}")
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail="Muitas operações deste tipo em sequência. Aguarde alguns segundos e tente novamente.",
        headers={"Retry-After": str(decision.retry_after)},
    )


def require_capability(user: dict, capability: str, action: str) -> None:
    if user["organization_permissions"].get(capability):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Seu perfil permite consultar dados, mas não {action}. Peça a um administrador para alterar seu acesso.",
    )


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


@app.get("/health/live")
def liveness() -> dict:
    return {"status": "ok"}


def readiness_report() -> dict:
    checks: dict[str, dict] = {}
    probes = {
        "receita_postgresql": repository.health_check,
        "contas": auth_store.health_check,
        "saas": saas_store.health_check,
        "processamentos": job_store.health_check,
    }
    for name, probe in probes.items():
        try:
            result = probe()
            checks[name] = result if isinstance(result, dict) else {"ok": bool(result)}
        except Exception:
            checks[name] = {"ok": False}
    checks["worker"] = {"ok": job_runner.is_alive()}
    ready = all(bool(component.get("ok")) for component in checks.values())
    dataset_version = checks.get("receita_postgresql", {}).get("dataset_version")
    return {"status": "ready" if ready else "degraded", "ready": ready, "dataset_version": dataset_version, "components": checks}


@app.get("/health/ready")
def readiness() -> Response:
    report = readiness_report()
    return JSONResponse(report, status_code=200 if report["ready"] else 503)


@app.get("/")
def index(request: Request) -> Response:
    return page_response(request, "index.html", next_path="/")


@app.get("/login")
def login_page(request: Request) -> Response:
    user, _ = authenticate_request(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return FileResponse(static_dir / "login.html")


@app.get("/plans")
def plans_page() -> Response:
    return FileResponse(static_dir / "plans.html")


@app.get("/produto")
def product_page() -> Response:
    return FileResponse(static_dir / "product.html")


@app.get("/termos")
def terms_page() -> Response:
    return FileResponse(static_dir / "legal.html")


@app.get("/privacidade")
def privacy_policy_page() -> Response:
    return FileResponse(static_dir / "legal.html")


@app.get("/billing/return")
def billing_return(status: str = Query("pending", pattern="^(success|cancel|expired|pending)$")) -> Response:
    return RedirectResponse(f"/?tab=billing&billing_return={status}", status_code=303)


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


@app.get("/admin")
def admin_page(request: Request) -> Response:
    return page_response(request, "admin.html", next_path="/admin")


@app.get("/account")
def account_page(request: Request) -> Response:
    return page_response(request, "account.html", next_path="/account")


@app.get("/help")
def help_page(request: Request) -> Response:
    return page_response(request, "help.html", next_path="/help")


@app.get("/organizations")
def organizations_page(request: Request) -> Response:
    return page_response(request, "organizations.html", next_path="/organizations")


@app.get("/invite")
def invite_page() -> Response:
    return FileResponse(static_dir / "invite.html")


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    user, _ = authenticate_request(request)
    mail_ready = mail_available(settings)
    signup_available = settings.saas_self_signup_enabled and mail_ready and legal_documents.configured
    signup_blocker = None
    if not settings.saas_self_signup_enabled:
        signup_blocker = "closed"
    elif not mail_ready:
        signup_blocker = "email"
    elif not legal_documents.configured:
        signup_blocker = "legal"
    return {
        "authenticated": bool(user),
        "identifier": user["identifier"] if user else None,
        "password_reset_available": mail_ready,
        "signup_available": signup_available,
        "signup_blocker": signup_blocker,
        "legal": legal_documents.public(),
    }


@app.get("/api/legal/documents")
def public_legal_documents() -> dict:
    return legal_documents.public()


@app.get("/api/legal/acceptances")
def current_legal_acceptances(user: dict = Depends(require_auth)) -> dict:
    return {"acceptances": auth_store.legal_acceptances(user["id"])}


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
    if not settings.saas_self_signup_enabled or not mail_available(settings) or not legal_documents.configured:
        raise HTTPException(status_code=503, detail="O cadastro público ainda não está disponível.")
    if not legal_documents.accepts(payload.terms_version, payload.privacy_version):
        raise HTTPException(
            status_code=409,
            detail="Os termos foram atualizados. Recarregue a página e revise as versões atuais.",
        )
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
        legal_versions={"terms": payload.terms_version, "privacy": payload.privacy_version},
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
    saas_store.record_product_event(
        organization_id,
        user["id"],
        "workspace.signup_verified",
        subject_type="organization",
        subject_id=str(organization_id),
        deduplication_key=f"workspace.signup_verified:{organization_id}",
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


@app.get("/api/privacy")
def privacy_summary(user: dict = Depends(require_auth)) -> Response:
    return JSONResponse(
        {
            **auth_store.privacy_summary(user["id"]),
            "account_export_available": True,
            "deletion_mode": "reviewed_request",
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/api/privacy/export")
def export_account_data(payload: PrivacyPasswordRequest, user: dict = Depends(require_auth)) -> Response:
    if not auth_store.verify_password(user["id"], payload.current_password):
        raise HTTPException(status_code=401, detail="A senha atual está incorreta.")
    exported = auth_store.account_data_export(user["id"])
    if not exported:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    body = json.dumps(
        {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Conta, sessões, vínculos com organizações, aceites jurídicos e ações administrativas do usuário.",
            **exported,
        },
        ensure_ascii=False,
        indent=2,
    )
    return Response(
        body,
        media_type="application/json; charset=utf-8",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Disposition": 'attachment; filename="echopjs-dados-da-conta.json"',
        },
    )


@app.post("/api/privacy/deletion-requests", status_code=201)
def create_deletion_request(payload: PrivacyDeletionRequest, user: dict = Depends(require_auth)) -> dict:
    request = auth_store.create_account_deletion_request(
        user["id"], current_password=payload.current_password, reason=payload.reason
    )
    if not request:
        raise HTTPException(status_code=401, detail="A senha atual está incorreta.")
    return request


@app.delete("/api/privacy/deletion-requests/current")
def cancel_deletion_request(user: dict = Depends(require_auth)) -> dict:
    request = auth_store.cancel_account_deletion_request(user["id"])
    if not request:
        raise HTTPException(status_code=404, detail="Não existe solicitação ativa para cancelar.")
    return request


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
    saas_store.record_product_event(
        org["id"],
        user["id"],
        "workspace.created",
        subject_type="organization",
        subject_id=str(org["id"]),
        deduplication_key=f"workspace.created:{org['id']}",
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


@app.put("/api/organizations/{org_id}/owner/{member_id}")
def transfer_organization_owner(org_id: int, member_id: int, user: dict = Depends(require_auth)) -> dict:
    if org_id == settings.saas_internal_organization_id:
        raise HTTPException(status_code=409, detail="A responsabilidade da organização interna EchoHub é protegida.")
    owner = auth_store.transfer_organization_ownership(user["id"], org_id, member_id)
    return {"transferred": True, "owner": owner}


@app.post("/api/organizations/{org_id}/invitations", status_code=201)
def create_invitation(org_id: int, payload: InvitationRequest, user: dict = Depends(require_auth)) -> dict:
    key = f"invite-create:{user['id']}"
    if not invitation_rate_limiter.allowed(key, monotonic()):
        raise HTTPException(status_code=429, detail="Limite temporário de convites atingido. Tente mais tarde.")
    invite = auth_store.create_invitation(user["id"], org_id, payload.email, payload.role)
    saas_store.record_product_event(
        org_id,
        user["id"],
        "team.invitation_created",
        subject_type="invitation",
        subject_id=str(invite["id"]),
        deduplication_key=f"team.invitation_created:{invite['id']}",
    )
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
        return {
            **auth_store.invitation_preview(payload.token),
            "legal_acceptance_required": legal_documents.configured,
            "legal": legal_documents.public(),
        }
    except OrganizationError:
        login_rate_limiter.failed(key, monotonic())
        raise


@app.post("/api/invitations/accept")
def accept_invitation(payload: InvitationAcceptRequest, request: Request) -> Response:
    key = invitation_attempt(request, payload.token)
    legal_versions = None
    if legal_documents.configured:
        if not payload.accept_terms:
            raise HTTPException(status_code=422, detail="Leia e aceite os Termos de Uso e a Política de Privacidade.")
        if not legal_documents.accepts(payload.terms_version, payload.privacy_version):
            raise HTTPException(status_code=409, detail="Os documentos foram atualizados. Reabra o convite e revise as versões atuais.")
        legal_versions = {"terms": payload.terms_version, "privacy": payload.privacy_version}
    try:
        user, org_id = auth_store.accept_invitation(
            payload.token, payload.password, legal_versions=legal_versions,
        )
    except OrganizationError:
        login_rate_limiter.failed(key, monotonic())
        raise
    login_rate_limiter.succeeded(key)
    saas_store.record_product_event(
        org_id,
        user["id"],
        "team.invitation_accepted",
        subject_type="organization",
        subject_id=str(org_id),
        deduplication_key=f"team.invitation_accepted:{org_id}:{user['id']}",
    )
    auth_store.delete_session(request.cookies.get(SESSION_COOKIE))
    token, expires = auth_store.create_session(user["id"])
    response = JSONResponse({"accepted": True, "organization_id": org_id})
    set_session_cookie(response, token, expires)
    return response


@app.get("/api/billing/summary")
def billing_summary(user: dict = Depends(require_organization)) -> dict:
    return saas_store.billing_summary(user["organization_id"])


@app.get("/api/billing/catalog")
def billing_catalog_public() -> dict:
    catalog = current_billing_catalog()
    webhook_ready = 32 <= len(settings.asaas_webhook_token) <= 255
    provider_ready = (
        settings.saas_billing_provider == "asaas"
        and asaas_client.available
        and webhook_ready
    )
    return {
        "provider": settings.saas_billing_provider,
        "enabled": bool(settings.saas_billing_enabled and provider_ready),
        "configured": bool(catalog.offers()),
        "offers": [offer.public() for offer in catalog.offers()],
        "payment_methods": ["Pix", "Cartão de crédito"],
    }


@app.post("/api/billing/checkouts", status_code=201)
def create_billing_checkout(
    payload: BillingCheckoutRequest,
    request: Request,
    user: dict = Depends(require_organization),
) -> dict:
    if user["organization_role"] != "admin":
        raise HTTPException(status_code=403, detail="Somente administradores podem contratar ou alterar o plano.")
    if user["billing"]["is_internal"]:
        raise HTTPException(status_code=409, detail="A EchoHub já possui o plano interno com créditos ilimitados.")
    if not settings.saas_billing_enabled:
        raise PaymentError("O checkout ainda não foi ativado pela EchoHub.", 503)
    if settings.saas_billing_provider != "asaas":
        raise PaymentError("O provedor de cobrança configurado não é suportado.", 503)
    if not 32 <= len(settings.asaas_webhook_token) <= 255:
        raise PaymentError("O checkout aguarda a configuração segura dos webhooks.", 503)
    client_key = request.headers.get("idempotency-key")
    if client_key and (len(client_key) > 120 or not all(character.isalnum() or character in "-_:" for character in client_key)):
        raise HTTPException(status_code=422, detail="Chave de repetição inválida.")
    offer = current_billing_catalog().get(payload.plan_code)
    if offer.kind == "subscription":
        blocker = saas_store.subscription_purchase_blocker(
            user["organization_id"], client_key=client_key,
        )
        if blocker:
            if blocker["kind"] == "checkout":
                raise HTTPException(
                    status_code=409,
                    detail=f"Já existe um checkout de {blocker['plan_name']} aguardando conclusão. Continue pelo histórico de pagamentos.",
                )
            raise HTTPException(
                status_code=409,
                detail=f"A organização já possui a assinatura {blocker['plan_name']}. Cancele a renovação atual antes de contratar outra.",
            )
    order = saas_store.create_billing_order(
        user["organization_id"],
        user["id"],
        provider="asaas",
        kind=offer.kind,
        plan_code=offer.code,
        plan_name=offer.name,
        price_cents=offer.price_cents,
        credits=offer.credits,
        cycle=offer.cycle,
        client_key=client_key,
    )
    if order.get("checkout_url"):
        return order
    try:
        checkout = asaas_client.create_checkout(
            offer,
            external_reference=order["external_reference"],
            public_url=settings.app_public_url,
        )
    except PaymentError:
        saas_store.billing_checkout_failed(user["organization_id"], order["id"])
        raise
    return saas_store.billing_checkout_created(user["organization_id"], order["id"], **checkout)


@app.delete("/api/billing/subscription")
def cancel_billing_subscription(
    payload: BillingCancellationRequest,
    user: dict = Depends(require_organization),
) -> dict:
    if user["organization_role"] != "admin":
        raise HTTPException(status_code=403, detail="Somente administradores podem cancelar a assinatura.")
    if user["billing"]["is_internal"]:
        raise HTTPException(status_code=409, detail="O plano interno da EchoHub não possui cobrança recorrente.")
    if not auth_store.verify_password(user["id"], payload.current_password):
        raise HTTPException(status_code=401, detail="A senha atual está incorreta.")
    if (
        not settings.saas_billing_enabled
        or settings.saas_billing_provider != "asaas"
        or not asaas_client.available
        or not 32 <= len(settings.asaas_webhook_token) <= 255
    ):
        raise PaymentError("A gestão da assinatura ainda não foi ativada pela EchoHub.", 503)
    action = saas_store.begin_subscription_cancellation(
        user["organization_id"], user["id"], reason=payload.reason,
    )
    try:
        asaas_client.cancel_subscription(action["provider_subscription_id"])
    except PaymentError as error:
        saas_store.fail_subscription_cancellation(action["id"], str(error))
        raise
    subscription = saas_store.complete_subscription_cancellation(action["id"])
    return {
        "canceled": True,
        "subscription": subscription,
        "message": "A renovação foi cancelada. Os créditos que já estavam no saldo continuam disponíveis.",
    }


@app.post("/api/webhooks/asaas")
def receive_asaas_webhook(payload: dict, request: Request) -> dict:
    configured_token = settings.asaas_webhook_token
    received_token = request.headers.get("asaas-access-token", "")
    if not configured_token or not hmac.compare_digest(received_token, configured_token):
        raise HTTPException(status_code=401, detail="Webhook não autorizado.")
    event = normalize_asaas_event(payload)
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return saas_store.process_billing_event(provider="asaas", payload_digest=digest, **event)


@app.get("/api/admin/billing/events")
def admin_billing_events(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_internal_admin),
) -> dict:
    return {"events": saas_store.admin_billing_events(limit=limit)}


def _admin_billing_catalog_state() -> dict:
    state = saas_store.billing_catalog_state()

    def version_payload(version: dict | None) -> dict | None:
        if not version:
            return None
        catalog = BillingCatalog(version["catalog_json"])
        return {
            "revision": version["revision"],
            "status": version["status"],
            "created_by": version["created_by"],
            "created_at": version["created_at"],
            "published_by": version.get("published_by"),
            "published_at": version.get("published_at"),
            "offers": [offer.public() for offer in catalog.offers()],
        }

    active = current_billing_catalog()
    published = version_payload(state["published"])
    return {
        "source": "published" if published else ("deployment" if billing_catalog.offers() else "empty"),
        "active": published or {
            "revision": None,
            "status": "deployment" if billing_catalog.offers() else "empty",
            "created_by": None,
            "created_at": None,
            "published_by": None,
            "published_at": None,
            "offers": [offer.public() for offer in active.offers()],
        },
        "draft": version_payload(state["draft"]),
        "history": state["history"],
        "checkout_enabled": billing_catalog_public()["enabled"],
    }


@app.get("/api/admin/billing/catalog")
def admin_billing_catalog(user: dict = Depends(require_internal_admin)) -> dict:
    return _admin_billing_catalog_state()


@app.put("/api/admin/billing/catalog/draft")
def save_admin_billing_catalog_draft(
    payload: BillingCatalogDraftRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    raw_json = json.dumps(
        [offer.model_dump(mode="json") for offer in payload.offers],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        validated = BillingCatalog(raw_json)
    except PaymentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    canonical = json.dumps(
        [offer.public() for offer in validated.offers()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    saas_store.save_billing_catalog_draft(user["id"], canonical)
    return _admin_billing_catalog_state()


@app.post("/api/admin/billing/catalog/publish")
def publish_admin_billing_catalog(user: dict = Depends(require_internal_admin)) -> dict:
    state = saas_store.billing_catalog_state()
    if not state["draft"]:
        raise SaaSError("Salve um rascunho válido antes de publicar o catálogo.", 409)
    BillingCatalog(state["draft"]["catalog_json"])
    saas_store.publish_billing_catalog(user["id"])
    return _admin_billing_catalog_state()


@app.get("/api/admin/operations")
def admin_operations(user: dict = Depends(require_internal_admin)) -> dict:
    application = readiness_report()
    backup = backup_status(settings.saas_backup_status_path)
    billing = billing_catalog_public()
    catalog = current_billing_catalog()
    webhook_ready = 32 <= len(settings.asaas_webhook_token) <= 255
    provider_ready = settings.saas_billing_provider == "asaas" and asaas_client.available and webhook_ready
    return {
        "readiness": application,
        "traffic": operations_monitor.snapshot(),
        "backup": backup,
        "billing": {
            "enabled": billing["enabled"],
            "catalog_offers": len(catalog.offers()),
            "provider": settings.saas_billing_provider,
        },
        "launch": commercial_launch_readiness(
            settings,
            application_ready=application["ready"],
            backup=backup,
            email_ready=mail_available(settings),
            legal_ready=legal_documents.configured,
            billing_catalog_ready=billing["configured"],
            billing_provider_ready=provider_ready,
        ),
    }


@app.get("/api/admin/privacy/requests")
def admin_privacy_requests(
    status: str = Query("", max_length=30),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_internal_admin),
) -> dict:
    try:
        requests = auth_store.admin_privacy_requests(status=status, limit=limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return {"requests": requests}


@app.patch("/api/admin/privacy/requests/{request_id}")
def update_admin_privacy_request(
    request_id: str,
    payload: PrivacyAdminUpdateRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    request = auth_store.update_privacy_request(
        request_id,
        status=payload.status,
        resolution_note=payload.resolution_note,
        handled_by=user["id"],
    )
    if not request:
        raise HTTPException(status_code=404, detail="Solicitação de privacidade não encontrada.")
    return request


def support_dataset_version() -> str | None:
    try:
        value = repository.health_check().get("dataset_version")
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


@app.get("/api/support/context")
def support_context(user: dict = Depends(require_support_organization)) -> dict:
    organization = auth_store.organization_for_user(user["id"], user["organization_id"])
    return {
        "organization": {"id": organization["id"], "name": organization["name"]},
        "role": user["organization_role"],
        "plan_code": user["billing"]["plan_code"],
        "subscription_status": user["billing"]["subscription_status"],
        "credit_balance": user["billing"]["credit_balance"],
        "unlimited_credits": user["billing"]["unlimited_credits"],
        "dataset_version": support_dataset_version(),
    }


@app.get("/api/support/tickets")
def support_tickets(user: dict = Depends(require_support_organization)) -> dict:
    return {"tickets": saas_store.list_support_tickets(user["organization_id"])}


@app.post("/api/support/tickets", status_code=201)
def create_support_ticket(
    payload: SupportTicketRequest,
    request: Request,
    user: dict = Depends(require_support_organization),
) -> dict:
    return saas_store.create_support_ticket(
        user["organization_id"],
        user["id"],
        requester_identifier=user["identifier"],
        category=payload.category,
        priority=payload.priority,
        subject=payload.subject,
        message=payload.message,
        diagnostic={
            "request_id": request.state.request_id,
            "dataset_version": support_dataset_version(),
            "plan_code": user["billing"]["plan_code"],
            "subscription_status": user["billing"]["subscription_status"],
            "organization_role": user["organization_role"],
        },
    )


@app.get("/api/support/tickets/{ticket_id}")
def support_ticket_detail(
    ticket_id: str,
    user: dict = Depends(require_support_organization),
) -> dict:
    ticket = saas_store.support_ticket_detail(user["organization_id"], ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Chamado não encontrado.")
    return ticket


@app.post("/api/support/tickets/{ticket_id}/messages")
def reply_support_ticket(
    ticket_id: str,
    payload: SupportMessageRequest,
    user: dict = Depends(require_support_organization),
) -> dict:
    return saas_store.reply_support_ticket(
        user["organization_id"], ticket_id, user["id"],
        author_kind="customer", message=payload.message,
    )


@app.get("/api/admin/support/tickets")
def admin_support_tickets(
    status: str | None = Query(None),
    query: str = Query("", max_length=120),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_internal_admin),
) -> dict:
    result = saas_store.admin_support_tickets(status=status, query=query, limit=limit)
    organization_names: dict[int, str] = {}
    for ticket in result["tickets"]:
        organization_id = ticket["organization_id"]
        if organization_id not in organization_names:
            organization_names[organization_id] = auth_store.admin_organization(organization_id)["name"]
        ticket["organization_name"] = organization_names[organization_id]
    return result


@app.get("/api/admin/support/tickets/{ticket_id}")
def admin_support_ticket_detail(
    ticket_id: str,
    user: dict = Depends(require_internal_admin),
) -> dict:
    ticket = saas_store.admin_support_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Chamado não encontrado.")
    return ticket


@app.patch("/api/admin/support/tickets/{ticket_id}")
def admin_update_support_ticket(
    ticket_id: str,
    payload: SupportTicketAdminUpdateRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    return saas_store.update_support_ticket(
        ticket_id, status=payload.status, priority=payload.priority, actor_id=user["id"],
    )


@app.post("/api/admin/support/tickets/{ticket_id}/messages")
def admin_reply_support_ticket(
    ticket_id: str,
    payload: SupportMessageRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    ticket = saas_store.admin_support_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Chamado não encontrado.")
    return saas_store.reply_support_ticket(
        ticket["organization_id"], ticket_id, user["id"],
        author_kind="support", message=payload.message,
    )


@app.get("/api/dashboard")
def dashboard(user: dict = Depends(require_organization)) -> dict:
    summary = saas_store.dashboard_summary(user["organization_id"])
    jobs = job_store.list_jobs(limit=5, organization_id=user["organization_id"])
    summary["recent_jobs"] = jobs
    summary["active_jobs"] = job_store.active_job_count(organization_id=user["organization_id"])
    summary["organization_id"] = user["organization_id"]
    summary["onboarding"] = auth_store.organization_activation_summary(user["organization_id"])
    return summary


def _admin_product_report() -> tuple[dict, dict[int, dict]]:
    """Join auth and SaaS facts only after internal-admin authorization."""
    organizations = auth_store.admin_product_funnel_base()
    for organization in organizations:
        saas_store.ensure_organization(
            organization["id"],
            unlimited=organization["id"] == settings.saas_internal_organization_id,
            initial_credits=settings.saas_trial_credits,
        )
    product_activity = saas_store.admin_product_activity()
    customer_count = activated_count = converted_count = retained_count = 0
    active_last_30_days = registrations_last_30_days = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    enriched: dict[int, dict] = {}

    def recent(value: str | None) -> bool:
        if not value:
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed >= cutoff
        except (TypeError, ValueError):
            return False

    for organization in organizations:
        organization_id = organization["id"]
        activity = product_activity.get(organization_id, {})
        last_activity_at = max(
            filter(None, (activity.get("last_activity_at"), organization.get("last_team_activity_at"))),
            default=None,
        )
        activated = bool(
            activity.get("action_count")
            or organization["member_count"] > 1
            or organization["pending_invitation_count"]
        )
        converted = activity.get("plan_code") not in {None, "internal", "trial"}
        retained = int(activity.get("activity_days") or 0) >= 2
        reached_value = bool(activity.get("unlocked_companies"))
        stage = (
            "converted" if converted else
            "value" if reached_value else
            "activated" if activated else
            "registered"
        )
        enriched[organization_id] = {
            **activity,
            "activated": activated,
            "converted": converted,
            "retained": retained,
            "reached_value": reached_value,
            "stage": stage,
            "last_activity_at": last_activity_at,
        }
        if organization_id == settings.saas_internal_organization_id:
            continue
        customer_count += 1
        activated_count += int(activated)
        converted_count += int(converted)
        retained_count += int(retained)
        active_last_30_days += int(recent(last_activity_at))
        registrations_last_30_days += int(recent(organization["created_at"]))

    def rate(value: int) -> int:
        return round(100 * value / customer_count) if customer_count else 0

    funnel = {
        "registered_organizations": customer_count,
        "activated_organizations": activated_count,
        "converted_organizations": converted_count,
        "retained_organizations": retained_count,
        "active_last_30_days": active_last_30_days,
        "registrations_last_30_days": registrations_last_30_days,
        "activation_rate": rate(activated_count),
        "conversion_rate": rate(converted_count),
        "retention_rate": rate(retained_count),
    }
    return funnel, enriched


@app.get("/api/admin/overview")
def admin_overview(
    query: str = Query("", max_length=120),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_internal_admin),
) -> dict:
    funnel, activity = _admin_product_report()
    catalog = auth_store.admin_organization_catalog(query, limit=limit, offset=offset)
    for organization in catalog["organizations"]:
        organization["billing"] = saas_store.ensure_organization(
            organization["id"],
            unlimited=organization["id"] == settings.saas_internal_organization_id,
            initial_credits=settings.saas_trial_credits,
        )
        organization["unlocked_companies"] = saas_store.billing_summary(
            organization["id"], ledger_limit=0
        )["unlocked_companies"]
        organization["activity"] = activity.get(organization["id"], {})
    catalog["metrics"] = saas_store.admin_metrics()
    catalog["funnel"] = funnel
    catalog["billing_events"] = saas_store.admin_billing_events(limit=20)
    catalog["billing_emails"] = {
        **billing_email_dispatcher.status(),
        **saas_store.admin_billing_email_deliveries(limit=20),
    }
    return catalog


@app.post("/api/admin/customer-workspaces", status_code=201)
def provision_customer_workspace(
    payload: CustomerWorkspaceRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    key = f"customer-workspace:{user['id']}"
    if not invitation_rate_limiter.allowed(key, monotonic()):
        raise HTTPException(status_code=429, detail="Limite temporário de pilotos atingido. Tente mais tarde.")
    provisioned = auth_store.provision_customer_workspace(
        user["id"], payload.name, payload.owner_email,
    )
    organization = provisioned["organization"]
    initial_credits = settings.saas_trial_credits if payload.trial_credits is None else payload.trial_credits
    profile = saas_store.ensure_organization(
        organization["id"], initial_credits=initial_credits,
    )
    saas_store.record_product_event(
        organization["id"], user["id"], "workspace.created",
        subject_type="organization", subject_id=str(organization["id"]),
        deduplication_key=f"workspace.created:{organization['id']}",
    )
    invitation_rate_limiter.failed(key, monotonic())
    invitation = provisioned["invitation"]
    link = f"{settings.app_public_url.rstrip('/')}/invite#token={invitation.pop('token')}"
    delivery = send_invitation(
        settings, email=invitation["email"], organization_name=invitation["organization_name"], link=link,
    ) if payload.send_email else "manual"
    return {
        "organization": {**organization, "billing": profile},
        "invitation": {**invitation, "link": link, "delivery": delivery},
        "handoff": {
            "status": "waiting_acceptance",
            "next_step": "Depois que o cliente aceitar, transfira a responsabilidade na área Organizações e equipe.",
        },
    }


@app.post("/api/admin/organizations/{organization_id}/pilot-invitation", status_code=201)
def reissue_customer_pilot_invitation(
    organization_id: int,
    payload: InvitationDeliveryRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    key = f"pilot-invitation:{user['id']}"
    if not invitation_rate_limiter.allowed(key, monotonic()):
        raise HTTPException(status_code=429, detail="Limite temporário de convites atingido. Tente mais tarde.")
    invitation = auth_store.reissue_provisioned_owner_invitation(user["id"], organization_id)
    invitation_rate_limiter.failed(key, monotonic())
    link = f"{settings.app_public_url.rstrip('/')}/invite#token={invitation.pop('token')}"
    delivery = send_invitation(
        settings,
        email=invitation["email"],
        organization_name=invitation["organization_name"],
        link=link,
    ) if payload.send_email else "manual"
    saas_store.record_product_event(
        organization_id,
        user["id"],
        "team.pilot_invitation_reissued",
        subject_type="invitation",
        subject_id=str(invitation["id"]),
        deduplication_key=f"team.pilot_invitation_reissued:{invitation['id']}",
    )
    return {**invitation, "link": link, "delivery": delivery}


@app.get("/api/notifications")
def notifications(user: dict = Depends(require_organization)) -> dict:
    jobs = job_store.list_jobs(limit=50, organization_id=user["organization_id"])
    saas_store.sync_notifications(
        user["organization_id"],
        user["id"],
        jobs=jobs,
        low_credit_threshold=settings.saas_low_credit_threshold,
    )
    return saas_store.list_notifications(user["organization_id"], user["id"])


@app.post("/api/notifications/read-all")
def read_all_notifications(user: dict = Depends(require_organization)) -> dict:
    updated = saas_store.mark_all_notifications_read(user["organization_id"], user["id"])
    return {"updated": updated}


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: str, user: dict = Depends(require_organization)) -> dict:
    if not saas_store.mark_notification_read(user["organization_id"], user["id"], notification_id):
        raise HTTPException(status_code=404, detail="Notificação não encontrada.")
    return {"read": True}


@app.get("/api/admin/organizations/{organization_id}")
def admin_organization_detail(
    organization_id: int,
    user: dict = Depends(require_internal_admin),
) -> dict:
    organization = auth_store.admin_organization(organization_id)
    organization["billing"] = saas_store.ensure_organization(
        organization_id,
        unlimited=organization_id == settings.saas_internal_organization_id,
        initial_credits=settings.saas_trial_credits,
    )
    organization["commercial"] = saas_store.billing_summary(organization_id, ledger_limit=50)
    _, activity = _admin_product_report()
    organization["activity"] = activity.get(organization_id, {})
    return organization


@app.patch("/api/admin/organizations/{organization_id}/billing")
def update_admin_billing(
    organization_id: int,
    payload: BillingProfileUpdateRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    auth_store.admin_organization(organization_id)
    if organization_id == settings.saas_internal_organization_id and (
        payload.plan_code != "internal" or not payload.unlimited_credits or payload.subscription_status != "active"
    ):
        raise HTTPException(status_code=409, detail="O plano interno da EchoHub precisa permanecer ativo e ilimitado.")
    return saas_store.update_billing_profile(
        organization_id,
        plan_code=payload.plan_code,
        subscription_status=payload.subscription_status,
        unlimited_credits=payload.unlimited_credits,
        actor_id=user["id"],
    )


@app.post("/api/admin/organizations/{organization_id}/credits")
def adjust_admin_credits(
    organization_id: int,
    payload: CreditAdjustmentRequest,
    user: dict = Depends(require_internal_admin),
) -> dict:
    auth_store.admin_organization(organization_id)
    return saas_store.adjust_credits(
        organization_id,
        payload.amount,
        description=payload.description,
        actor_id=user["id"],
    )


@app.post("/api/credits/estimate")
def estimate_credits(payload: CompanySelectionRequest, user: dict = Depends(require_organization)) -> dict:
    return saas_store.credit_estimate(user["organization_id"], payload.cnpjs)


@app.get("/api/saved-searches")
def list_saved_searches(user: dict = Depends(require_organization)) -> dict:
    return {"saved_searches": saas_store.list_saved_searches(user["organization_id"])}


@app.post("/api/saved-searches", status_code=201)
def create_saved_search(payload: SavedSearchRequest, user: dict = Depends(require_organization)) -> dict:
    require_capability(user, "manage_library", "salvar buscas")
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


@app.put("/api/saved-searches/{search_id}")
def update_saved_search(
    search_id: str,
    payload: SavedSearchRequest,
    user: dict = Depends(require_organization),
) -> dict:
    require_capability(user, "manage_library", "alterar buscas salvas")
    return saas_store.update_saved_search(
        user["organization_id"],
        search_id,
        user["id"],
        name=payload.name,
        filters=payload.filters.model_dump(mode="json"),
        result_count=payload.result_count,
    )


@app.post("/api/saved-searches/{search_id}/runs")
def record_saved_search_run(
    search_id: str,
    payload: SavedSearchRunRequest,
    user: dict = Depends(require_organization),
) -> dict:
    return saas_store.record_saved_search_run(
        user["organization_id"], search_id, payload.result_count, actor_id=user["id"]
    )


@app.delete("/api/saved-searches/{search_id}")
def delete_saved_search(search_id: str, user: dict = Depends(require_organization)) -> dict:
    require_capability(user, "manage_library", "excluir buscas salvas")
    if not saas_store.delete_saved_search(user["organization_id"], search_id):
        raise HTTPException(status_code=404, detail="Busca salva não encontrada.")
    return {"deleted": True}


@app.get("/api/company-lists")
def list_company_lists(user: dict = Depends(require_organization)) -> dict:
    return {"lists": saas_store.list_company_lists(user["organization_id"])}


@app.post("/api/company-lists", status_code=201)
def create_company_list(payload: CompanyListRequest, user: dict = Depends(require_organization)) -> dict:
    require_capability(user, "manage_library", "criar listas")
    return saas_store.create_company_list(
        user["organization_id"],
        user["id"],
        name=payload.name,
        description=payload.description,
    )


@app.get("/api/company-lists/{list_id}")
def get_company_list(
    list_id: str,
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_organization),
) -> dict:
    company_list = saas_store.company_list_detail(
        user["organization_id"], list_id, query=q, limit=limit, offset=offset,
    )
    if not company_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada.")
    return company_list


@app.get("/api/company-lists/{list_id}/export.csv")
def export_company_list(list_id: str, user: dict = Depends(require_organization)) -> Response:
    require_capability(user, "export", "exportar listas")
    enforce_heavy_rate_limit(user, "exports")
    company_list = saas_store.company_list_detail(user["organization_id"], list_id)
    if not company_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada.")
    content = export_companies_csv(company_list["companies"])
    credit = saas_store.credit_estimate(user["organization_id"], [])
    saas_store.record_product_event(
        user["organization_id"], user["id"], "company_list.exported",
        subject_type="company_list", subject_id=list_id,
        metadata={"company_count": company_list["company_count"]},
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="lista-empresas.csv"',
            "X-Credits-Spent": "0",
            "X-Credit-Balance": str(credit["credit_balance"]),
            "X-Unlimited-Credits": str(credit["unlimited_credits"]).lower(),
        },
    )


@app.put("/api/company-lists/{list_id}")
def update_company_list(
    list_id: str,
    payload: CompanyListRequest,
    user: dict = Depends(require_organization),
) -> dict:
    require_capability(user, "manage_library", "alterar listas")
    return saas_store.update_company_list(
        user["organization_id"], list_id, user["id"],
        name=payload.name, description=payload.description,
    )


@app.post("/api/company-lists/{list_id}/companies")
def add_companies_to_list(
    list_id: str,
    payload: CompanySelectionRequest,
    user: dict = Depends(require_organization),
) -> dict:
    require_capability(user, "manage_library", "alterar listas")
    enforce_heavy_rate_limit(user, "company-data")
    found = repository.companies_by_cnpjs(payload.cnpjs)
    companies = [found[cnpj] for cnpj in payload.cnpjs if cnpj in found]
    if len(companies) != len(payload.cnpjs):
        raise HTTPException(status_code=409, detail="Uma ou mais empresas não estão disponíveis na versão atual da Receita.")
    return saas_store.add_companies(
        user["organization_id"], list_id, user["id"], companies
    )


@app.post("/api/exports/companies")
def export_companies(payload: CompanySelectionRequest, user: dict = Depends(require_organization)) -> Response:
    require_capability(user, "export", "exportar empresas ou usar créditos")
    enforce_heavy_rate_limit(user, "exports")
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
    require_capability(user, "manage_library", "alterar listas")
    normalized = normalize_cnpj_identifier(cnpj)
    if not saas_store.remove_company(user["organization_id"], list_id, normalized):
        raise HTTPException(status_code=404, detail="Empresa não encontrada nesta lista.")
    return {"removed": True}


@app.delete("/api/company-lists/{list_id}")
def delete_company_list(list_id: str, user: dict = Depends(require_organization)) -> dict:
    require_capability(user, "manage_library", "excluir listas")
    if not saas_store.delete_company_list(user["organization_id"], list_id):
        raise HTTPException(status_code=404, detail="Lista não encontrada.")
    return {"deleted": True}


@app.post("/api/matches/batch")
def matches(payload: BatchRequest, user: dict = Depends(require_organization)) -> dict:
    enforce_heavy_rate_limit(user, "matching")
    if len(payload.items) > settings.max_batch_size:
        raise HTTPException(status_code=422, detail=f"maximo de {settings.max_batch_size} itens")
    items = [item.model_dump() for item in payload.items]
    result = matching_service.match_items(
        items,
        active_only=payload.active_only,
        check_website=payload.check_website,
    )
    saas_store.record_product_event(
        user["organization_id"], user["id"], "match.executed",
        metadata={"submitted": len(items)},
    )
    return result


@app.post("/api/jobs")
def create_job(payload: JobRequest, user: dict = Depends(require_organization)) -> dict:
    require_capability(user, "run_jobs", "criar processamentos em massa")
    enforce_heavy_rate_limit(user, "job-creation")
    if len(payload.items) > settings.max_job_size:
        raise HTTPException(status_code=422, detail=f"maximo de {settings.max_job_size} itens")
    items = [item.model_dump() for item in payload.items]
    try:
        job = job_store.create_job(
            payload.filename,
            items,
            active_only=payload.active_only,
            check_website=payload.check_website,
            organization_id=user["organization_id"],
            created_by=user["id"],
            max_active_jobs=settings.saas_max_active_jobs_per_organization,
        )
    except JobCapacityError as error:
        raise HTTPException(
            status_code=429,
            detail=f"{error} Aguarde a conclusão antes de enviar outro arquivo.",
            headers={"Retry-After": "30"},
        ) from error
    saas_store.record_product_event(
        user["organization_id"],
        user["id"],
        "job.created",
        subject_type="job",
        subject_id=job["id"],
        metadata={"submitted": len(items)},
        deduplication_key=f"job.created:{job['id']}",
        occurred_at=job["created_at"],
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
    require_capability(user, "export", "baixar resultados ou usar créditos")
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


def _company_search_response(
    payload: CompanySearchRequest,
    user: dict,
    *,
    preview: bool,
) -> dict:
    filters = payload.model_dump()
    filters["limit"] = 10000
    requested_list_ids = set(payload.included_list_ids) | set(payload.excluded_list_ids)
    available_list_ids = {
        item["id"] for item in saas_store.list_company_lists(user["organization_id"])
    }
    if requested_list_ids - available_list_ids:
        raise HTTPException(status_code=422, detail="Uma ou mais listas selecionadas não existem neste workspace.")
    included_cnpjs: set[str] | None = set(payload.included_cnpjs) if payload.included_cnpjs else None
    excluded_cnpjs: set[str] = set(payload.excluded_cnpjs)
    if payload.included_list_ids:
        list_cnpjs = saas_store.company_list_cnpjs(
            user["organization_id"], payload.included_list_ids,
        )
        included_cnpjs = list_cnpjs if included_cnpjs is None else included_cnpjs & list_cnpjs
    if payload.excluded_list_ids:
        excluded_cnpjs.update(saas_store.company_list_cnpjs(
            user["organization_id"], payload.excluded_list_ids,
        ))
    if payload.saved_status != "all":
        saved_cnpjs = saas_store.company_list_cnpjs(user["organization_id"])
        if payload.saved_status == "saved":
            included_cnpjs = saved_cnpjs if included_cnpjs is None else included_cnpjs & saved_cnpjs
        else:
            excluded_cnpjs.update(saved_cnpjs)
    if included_cnpjs is not None:
        included_cnpjs.difference_update(excluded_cnpjs)
        filters["_included_cnpjs"] = sorted(included_cnpjs)
    if excluded_cnpjs:
        filters["_excluded_cnpjs"] = sorted(excluded_cnpjs)
    try:
        results, capabilities, duration_ms, has_more, total_count = repository.search_companies(filters)
    except SearchCapabilityUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except QueryCanceled as error:
        raise HTTPException(
            status_code=408,
            detail="A busca ficou ampla demais. Acrescente uma regiao, UF ou CNAE e tente novamente.",
        ) from error
    memberships = saas_store.company_list_memberships(
        user["organization_id"],
        [company["cnpj"] for company in results],
    )
    enriched_results = []
    for company in results:
        saved_lists = memberships.get(company["cnpj"], [])
        enriched_results.append({
            **company,
            "saved": bool(saved_lists),
            "saved_lists": saved_lists,
        })
    saved_count = sum(1 for company in enriched_results if company["saved"])
    response = {
        "results": enriched_results,
        "returned": len(enriched_results),
        "total_count": total_count,
        "limit": filters["limit"],
        "has_more": has_more,
        "preview": preview,
        "segments": {
            "total": len(enriched_results),
            "new": len(enriched_results) - saved_count,
            "saved": saved_count,
        },
        "dataset_version": repository.current_version(),
        "capabilities": capabilities.as_dict(),
        "timing_ms": duration_ms,
    }
    if not preview:
        saas_store.record_product_event(
            user["organization_id"],
            user["id"],
            "search.executed",
            metadata={"returned": len(enriched_results), "total_count": total_count, "limit": filters["limit"]},
        )
    return response


@app.post("/api/search")
def search_companies(payload: CompanySearchRequest, user: dict = Depends(require_organization)) -> dict:
    enforce_heavy_rate_limit(user, "search")
    return _company_search_response(payload, user, preview=False)


@app.post("/api/search/preview")
def preview_companies(payload: CompanySearchRequest, user: dict = Depends(require_organization)) -> dict:
    enforce_heavy_rate_limit(user, "search-preview")
    return _company_search_response(payload, user, preview=True)


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
def explorer_company_lookup(payload: CompanyLookupRequest, user: dict = Depends(require_organization)) -> dict:
    enforce_heavy_rate_limit(user, "company-data")
    result = company_lookup_results(payload.cnpjs)
    saas_store.record_product_event(
        user["organization_id"],
        user["id"],
        "company_lookup.executed",
        metadata={"submitted": len(payload.cnpjs), "found": result["found"]},
    )
    return result


@app.post("/api/exports/cnpj-lookup")
def export_cnpj_lookup(payload: CompanyLookupRequest, user: dict = Depends(require_organization)) -> Response:
    require_capability(user, "export", "exportar empresas ou usar créditos")
    enforce_heavy_rate_limit(user, "exports")
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
