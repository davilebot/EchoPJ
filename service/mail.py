import smtplib
import ssl
from email.message import EmailMessage


def mail_available(settings):
    return bool(settings.smtp_host and settings.smtp_from)


def send_invitation(settings, *, email, organization_name, link):
    """Only send via TLS. Never log tokens or SMTP exceptions/credentials."""
    if not mail_available(settings):
        return "manual"
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "Convite para uma organização no EchoPJs"
    message.set_content(
        f"Você foi convidado(a) para {organization_name} no EchoPJs.\n\n"
        f"Crie sua conta ou entre com sua senha atual:\n{link}\n\n"
        "O convite vale por 7 dias e só pode ser usado uma vez. Se não esperava este convite, ignore esta mensagem."
    )
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
