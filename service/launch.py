"""Safe, actionable commercial-launch readiness for the internal admin."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def _commercial_https_url(value: str) -> bool:
    parsed = urlsplit(str(value or "").strip())
    hostname = (parsed.hostname or "").casefold()
    return bool(
        parsed.scheme == "https"
        and hostname
        and hostname not in {"localhost", "127.0.0.1"}
        and not hostname.endswith(".easypanel.host")
    )


def commercial_launch_readiness(
    settings: Any,
    *,
    application_ready: bool,
    backup: dict[str, Any],
    email_ready: bool,
    legal_ready: bool,
    billing_catalog_ready: bool,
    billing_provider_ready: bool,
) -> dict[str, Any]:
    """Return only booleans and user-safe guidance; never expose credentials."""
    production_billing = "sandbox" not in str(settings.asaas_api_url).casefold()
    billing_ready = bool(
        settings.saas_billing_enabled
        and billing_catalog_ready
        and billing_provider_ready
        and production_billing
    )
    signup_ready = bool(settings.saas_self_signup_enabled and email_ready and legal_ready)
    domain_ready = _commercial_https_url(settings.app_public_url)
    checks = [
        {
            "key": "application", "category": "Produto", "label": "Aplicação e base",
            "ready": bool(application_ready),
            "detail": "APIs, bancos e worker respondendo." if application_ready else "Revise o diagnóstico de prontidão antes de receber clientes.",
        },
        {
            "key": "domain", "category": "Produto", "label": "Domínio comercial",
            "ready": domain_ready,
            "detail": "Domínio HTTPS comercial configurado." if domain_ready else "Aponte um domínio próprio HTTPS e atualize APP_PUBLIC_URL.",
            "action_url": "/produto",
        },
        {
            "key": "email", "category": "Aquisição", "label": "E-mail transacional",
            "ready": bool(email_ready),
            "detail": "Envio de confirmação, convites e recuperação disponível." if email_ready else "Configure SMTP_HOST e SMTP_FROM com entrega autenticada.",
            "action_url": "/forgot-password",
        },
        {
            "key": "legal", "category": "Aquisição", "label": "Termos e Privacidade",
            "ready": bool(legal_ready),
            "detail": "Identidade, vigência e versões publicadas." if legal_ready else "Preencha a identidade da operadora e publique as versões aprovadas.",
            "action_url": "/termos",
        },
        {
            "key": "signup", "category": "Aquisição", "label": "Cadastro público",
            "ready": signup_ready,
            "detail": "Cadastro com confirmação de e-mail e aceite disponível." if signup_ready else "Ative SAAS_SELF_SIGNUP_ENABLED após concluir e-mail e documentos.",
            "action_url": "/signup",
        },
        {
            "key": "catalog", "category": "Receita", "label": "Catálogo e preços",
            "ready": bool(billing_catalog_ready),
            "detail": "Ofertas comerciais disponíveis para contratação." if billing_catalog_ready else "Defina planos, preços, créditos e validade no catálogo.",
            "action_url": "/plans",
        },
        {
            "key": "billing", "category": "Receita", "label": "Cobrança em produção",
            "ready": billing_ready,
            "detail": "Checkout de produção ativado e autenticado." if billing_ready else (
                "Cadastre as credenciais e o webhook do provedor." if not billing_provider_ready else
                "Homologue o checkout e troque o ambiente Sandbox por produção." if not production_billing else
                "Ative SAAS_BILLING_ENABLED após a homologação."
            ),
            "action_url": "/plans",
        },
        {
            "key": "backup", "category": "Operação", "label": "Backup local verificado",
            "ready": backup.get("status") == "ok",
            "detail": "Cópia consistente recente e retenção acompanhada." if backup.get("status") == "ok" else "Restaure a rotina de backup local e confirme uma execução.",
        },
        {
            "key": "offsite_backup", "category": "Operação", "label": "Cópia fora do VPS",
            "ready": bool(settings.saas_offsite_backup_configured),
            "detail": "Cópia externa criptografada confirmada." if settings.saas_offsite_backup_configured else "Configure uma cópia criptografada em outro provedor e valide restauração.",
        },
        {
            "key": "external_alerts", "category": "Operação", "label": "Alertas externos",
            "ready": bool(settings.saas_external_alerts_configured),
            "detail": "Falhas críticas avisam o canal operacional." if settings.saas_external_alerts_configured else "Escolha e configure o canal externo para falhas e indisponibilidade.",
        },
    ]
    ready_count = sum(bool(item["ready"]) for item in checks)
    return {
        "ready": ready_count == len(checks),
        "ready_count": ready_count,
        "total_count": len(checks),
        "blocker_count": len(checks) - ready_count,
        "checks": checks,
    }
