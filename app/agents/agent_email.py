"""Shared email sending for agents (reuses existing SMTP config)."""
import logging
import traceback
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


async def send_agent_email(
    subject: str,
    html_body: str,
    inline_images: dict[str, Path] | None = None,
    extra_recipients: list[str] | None = None,
) -> None:
    """Send an HTML email to the configured recipients.

    Args:
        subject: Email subject
        html_body: HTML body (use cid:KEY in img src for inline images)
        inline_images: Dict of {cid_key: file_path} for inline image attachments
        extra_recipients: Additional emails to include (merged with settings.email_recipients, deduped)
    """
    if not settings.email_recipients or not settings.smtp_host:
        logger.warning("Email not configured, skipping agent email")
        return

    # Merge base recipients with any extras (preserve order, dedupe case-insensitively)
    recipients: list[str] = []
    seen: set[str] = set()
    for addr in list(settings.email_recipients) + list(extra_recipients or []):
        key = addr.lower().strip()
        if key and key not in seen:
            seen.add(key)
            recipients.append(addr)

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = settings.email_sender
    msg["To"] = ", ".join(recipients)

    msg_alt = MIMEMultipart("alternative")
    msg.attach(msg_alt)
    msg_alt.attach(MIMEText(html_body, "html"))

    # Attach inline images
    if inline_images:
        for cid, filepath in inline_images.items():
            if filepath.exists():
                img_data = filepath.read_bytes()
                img = MIMEImage(img_data, _subtype="png")
                img.add_header("Content-ID", f"<{cid}>")
                img.add_header("Content-Disposition", "inline", filename=filepath.name)
                msg.attach(img)

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=True,
            recipients=recipients,
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
            <h2 style="margin:0;">{agent_name} ran into trouble</h2>
            <p style="margin:4px 0 0;opacity:0.9;">Sorry boss, I hit a wall on my last run. Here's what happened:</p>
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
            {agent_name} | {settings.server_base_url}
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[{agent_name}] Oops — {type(error).__name__}",
        html_body=html,
    )
