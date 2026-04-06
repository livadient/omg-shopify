"""Shared email sending for agents (reuses existing SMTP config)."""
import logging
import traceback
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


async def send_error_email(agent_name: str, error: Exception, context: str = "") -> None:
    """Send an error notification email when an agent fails."""
    tb = traceback.format_exception(type(error), error, error.__traceback__)
    tb_text = "".join(tb)

    html = f"""
    <div style="font-family:sans-serif;max-width:650px;margin:0 auto;">
        <div style="background:#dc2626;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Agent Error: {agent_name}</h2>
        </div>
        <div style="padding:20px;border:1px solid #e5e7eb;">
            <p><strong>Error:</strong> {type(error).__name__}: {error}</p>
            {f'<p><strong>Context:</strong> {context}</p>' if context else ''}
            <details style="margin-top:16px;">
                <summary style="cursor:pointer;color:#2563eb;">Full Traceback</summary>
                <pre style="background:#f9fafb;padding:12px;border-radius:6px;overflow-x:auto;font-size:12px;">{tb_text}</pre>
            </details>
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            OMG AI Agents | {settings.server_base_url}
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[OMG ERROR] {agent_name} failed: {type(error).__name__}",
        html_body=html,
    )
