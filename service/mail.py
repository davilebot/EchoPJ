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
