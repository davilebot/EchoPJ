import smtplib
import ssl
from email.message import EmailMessage


def mail_available(settings):
    return bool(settings.smtp_host and settings.smtp_from)


def _send_message(settings, message):
    """Only send via TLS. Never log links, tokens, SMTP exceptions or credentials."""
    if not mail_available(settings):
        return "manual"
    try:
        context = ssl.create_default_context()
        if settings.smtp_ssl:
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10, context=context)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
        with client:
            if not settings.smtp_ssl:
                client.starttls(context=context)
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException):
        return "failed"
    return "sent"


def send_invitation(settings, *, email, organization_name, link):
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "Convite para uma organização no EchoPJs"
    message.set_content(
        f"Você foi convidado(a) para {organization_name} no EchoPJs.\n\n"
        f"Crie sua conta ou entre com sua senha atual:\n{link}\n\n"
        "O convite vale por 7 dias e só pode ser usado uma vez. Se não esperava este convite, ignore esta mensagem."
    )
    return _send_message(settings, message)


def send_password_reset(settings, *, email, link, valid_minutes):
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "Redefinição de senha do EchoPJs"
    message.set_content(
        "Recebemos um pedido para redefinir a senha da sua conta no EchoPJs.\n\n"
        f"Escolha uma nova senha neste link:\n{link}\n\n"
        f"O link vale por {valid_minutes} minutos e só pode ser usado uma vez. "
        "Se você não pediu esta alteração, ignore esta mensagem."
    )
    return _send_message(settings, message)


def send_signup_verification(settings, *, email, link, valid_hours):
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "Confirme seu cadastro no EchoPJs"
    message.set_content(
        "Seu workspace no EchoPJs está quase pronto.\n\n"
        f"Confirme seu e-mail e crie a organização neste link:\n{link}\n\n"
        f"O link vale por {valid_hours} horas e só pode ser usado uma vez. "
        "Se você não iniciou este cadastro, ignore esta mensagem."
    )
    return _send_message(settings, message)


def send_billing_alert(settings, *, email, organization_name, kind, plan_name, due_date, link):
    content = {
        "renewal": (
            "Sua assinatura do EchoPJs renova em breve",
            f"A assinatura {plan_name} de {organization_name} tem renovação prevista para {due_date}.",
            "Confira o plano e acompanhe a cobrança pelo link abaixo.",
        ),
        "due": (
            "Renovação do EchoPJs prevista para hoje",
            f"A assinatura {plan_name} de {organization_name} tem renovação prevista para hoje, {due_date}.",
            "A confirmação aparecerá no histórico financeiro assim que for processada.",
        ),
        "past_due": (
            "Pagamento pendente no EchoPJs",
            f"A renovação da assinatura {plan_name} de {organization_name} ainda não foi confirmada.",
            "Abra o histórico financeiro para consultar a cobrança e regularizar o próximo ciclo.",
        ),
    }
    if kind not in content:
        return "failed"
    subject, introduction, guidance = content[kind]
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = subject
    message.set_content(
        f"{introduction}\n\n{guidance}\n{link}\n\n"
        "Se outra pessoa cuida da cobrança, encaminhe esta mensagem ao administrador responsável."
    )
    return _send_message(settings, message)
