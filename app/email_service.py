"""Send email notifications for new orders."""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


async def send_order_notification(
    order_number: str | int,
    customer_name: str,
    order_total: str,
    currency: str,
    items: list[dict],
) -> None:
    """Send an HTML email with order details and cart/Qstomizer links.

    Args:
        order_number: Shopify order number
        customer_name: Customer full name
        order_total: Total price string
        currency: Currency code (e.g. "EUR")
        items: List of dicts, each with: title, variant_title, quantity,
               qstomizer_url, cart_url (may be None on error)
    """
    if not settings.email_recipients:
        logger.warning("No email recipients configured, skipping notification")
        return
    if not settings.smtp_host:
        logger.warning("No SMTP host configured, skipping notification")
        return

    subject = f"New OMG Order #{order_number} — {customer_name}"
    html = _build_html(order_number, customer_name, order_total, currency, items)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_sender
    msg["To"] = ", ".join(settings.email_recipients)
    msg.attach(MIMEText(html, "html"))

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
        logger.info(f"Order #{order_number} notification sent to {settings.email_recipients}")
    except Exception:
        logger.exception(f"Failed to send email for order #{order_number}")


def _build_html(
    order_number: str | int,
    customer_name: str,
    order_total: str,
    currency: str,
    items: list[dict],
) -> str:
    rows = ""
    for item in items:
        title = item.get("title", "")
        size = item.get("variant_title", "")
        qty = item.get("quantity", 1)
        cart_url = item.get("cart_url")
        qstomizer_url = item.get("qstomizer_url", "")
        error = item.get("error")

        if cart_url:
            link = f'<a href="{cart_url}" style="color:#2563eb;font-weight:bold;">Open Cart &rarr;</a>'
        elif qstomizer_url:
            link = f'<a href="{qstomizer_url}" style="color:#d97706;">Manual Qstomizer Link</a>'
        else:
            link = '<span style="color:#dc2626;">No link available</span>'

        if error:
            link += f'<br><small style="color:#dc2626;">Error: {error}</small>'

        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{title}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{size}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{qty}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{link}</td>
        </tr>"""

    manual_order_url = f"http://40.81.137.193:{settings.port}/manual-order"
    has_errors = any(item.get("error") for item in items)
    manual_order_note = ""
    if has_errors:
        manual_order_note = f"""
        <p style="margin-top:16px;padding:12px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;color:#991b1b;font-size:14px;">
            <strong>Some items failed automation.</strong> Use the
            <a href="{manual_order_url}" style="color:#2563eb;font-weight:bold;">Manual Order page</a>
            to retry them.
        </p>
        """

    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
        <h2 style="color:#111;">New Order #{order_number}</h2>
        <p><strong>Customer:</strong> {customer_name}</p>
        <p><strong>Total:</strong> {currency} {order_total}</p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px;">
            <thead>
                <tr style="background:#f3f4f6;">
                    <th style="padding:8px;text-align:left;">Product</th>
                    <th style="padding:8px;text-align:left;">Size</th>
                    <th style="padding:8px;text-align:left;">Qty</th>
                    <th style="padding:8px;text-align:left;">Action</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>{manual_order_note}
        <p style="margin-top:24px;color:#6b7280;font-size:13px;">
            Cart links open TShirtJunkies with the customized item ready for checkout.
            If the automated link failed, use the manual Qstomizer link to upload the design,
            or go to the <a href="{manual_order_url}" style="color:#2563eb;">Manual Order page</a>.
        </p>
    </div>
    """
