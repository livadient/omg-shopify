"""Shared email sending for agents (reuses existing SMTP config)."""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


async def send_agent_email(subject: str, html_body: str) -> None:
    """Send an HTML email to the configured recipients."""
    if not settings.email_recipients or not settings.smtp_host:
        logger.warning("Email not configured, skipping agent email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_sender
    msg["To"] = ", ".join(settings.email_recipients)
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=True,
            recipients=settings.email_recipients,
        )
        logger.info(f"Agent email sent: {subject}")
    except Exception:
        logger.exception(f"Failed to send agent email: {subject}")
