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
    shipping: dict | None = None,
) -> None:
    """Send an HTML email with order details and cart/Qstomizer links.

    Args:
        order_number: Shopify order number
        customer_name: Customer full name
        order_total: Total price string
        currency: Currency code (e.g. "EUR")
        items: List of dicts, each with: title, variant_title, quantity,
               qstomizer_url, cart_url (may be None on error)
        shipping: Customer shipping details dict (optional)
    """
    if not settings.email_recipients:
        logger.warning("No email recipients configured, skipping notification")
        return
    if not settings.smtp_host:
        logger.warning("No SMTP host configured, skipping notification")
        return

    subject = f"New OMG Order #{order_number} — {customer_name}"
    html = _build_html(order_number, customer_name, order_total, currency, items, shipping)

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
    shipping: dict | None = None,
) -> str:
    rows = ""
    for item in items:
        title = item.get("title", "")
        size = item.get("variant_title", "")
        qty = item.get("quantity", 1)
        cart_url = item.get("cart_url")
        qstomizer_url = item.get("qstomizer_url", "")
        error = item.get("error")

        mockup_url = item.get("mockup_url")

        if cart_url:
            link = f'<a href="{cart_url}" style="color:#2563eb;font-weight:bold;">Open Cart &rarr;</a>'
        elif qstomizer_url:
            link = f'<a href="{qstomizer_url}" style="color:#d97706;">Manual Qstomizer Link</a>'
        else:
            link = '<span style="color:#dc2626;">No link available</span>'

        if error:
            link += f'<br><small style="color:#dc2626;">Error: {error}</small>'

        mockup_mismatch = item.get("mockup_mismatch")
        if mockup_mismatch:
            link += (
                f'<br><span style="display:inline-block;margin-top:4px;padding:4px 8px;'
                f'background:#fef2f2;border:1px solid #fca5a5;border-radius:4px;'
                f'color:#991b1b;font-size:12px;font-weight:bold;">'
                f'⚠ DESIGN MISMATCH: {mockup_mismatch}</span>'
            )

        # Show Qstomizer rendered mockup (hosted on Shopify CDN, works in any email client)
        preview_img = ""
        if mockup_url:
            preview_img = f'<br><a href="{mockup_url}"><img src="{mockup_url}" style="max-width:150px;margin-top:4px;border-radius:4px;" alt="mockup"></a>'

        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{title}{preview_img}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{size}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{qty}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{link}</td>
        </tr>"""

    manual_order_url = f"{settings.server_base_url}/manual-order"
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

    # Build shipping details block for easy copy-paste at checkout
    shipping_html = ""
    if shipping:
        ship_fields = [
            ("Name", f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()),
            ("Address", shipping.get("address1", "")),
            ("City", shipping.get("city", "")),
            ("Zip", shipping.get("zip", "")),
            ("Country", shipping.get("country_code", "")),
            ("Phone", shipping.get("phone", "")),
            ("Email", shipping.get("email", "")),
        ]
        ship_rows = "".join(
            f"<tr><td style='padding:4px 8px;color:#6b7280;'>{k}:</td>"
            f"<td style='padding:4px 8px;font-weight:bold;'>{v}</td></tr>"
            for k, v in ship_fields if v
        )
        shipping_html = f"""
        <div style="margin-top:16px;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">
            <strong style="font-size:14px;">Shipping Details (copy to TJ checkout):</strong>
            <table style="margin-top:8px;">{ship_rows}</table>
        </div>"""

    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
        <h2 style="color:#111;">New Order #{order_number}</h2>
        <p><strong>Customer:</strong> {customer_name}</p>
        <p><strong>Total:</strong> {currency} {order_total}</p>{shipping_html}
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
