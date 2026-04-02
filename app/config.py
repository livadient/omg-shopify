import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    shopify_webhook_secret: str = ""
    tshirtjunkies_base_url: str = "https://tshirtjunkies.co"
    host: str = "0.0.0.0"
    port: int = 8000
    # SMTP / Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_sender: str = ""
    email_recipients: list[str] = []
    # OMG Shopify Admin API
    omg_shopify_domain: str = "52922c-2.myshopify.com"
    omg_shopify_admin_token: str = ""
    omg_shopify_client_id: str = ""
    omg_shopify_client_secret: str = ""
    ngrok_domain: str = ""


def _parse_recipients(raw: str) -> list[str]:
    return [r.strip() for r in raw.split(",") if r.strip()]


settings = Settings(
    shopify_webhook_secret=os.getenv("SHOPIFY_WEBHOOK_SECRET", ""),
    tshirtjunkies_base_url=os.getenv("TSHIRTJUNKIES_BASE_URL", "https://tshirtjunkies.co"),
    host=os.getenv("HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "8000")),
    smtp_host=os.getenv("SMTP_HOST", ""),
    smtp_port=int(os.getenv("SMTP_PORT", "587")),
    smtp_username=os.getenv("SMTP_USERNAME", ""),
    smtp_password=os.getenv("SMTP_PASSWORD", ""),
    email_sender=os.getenv("EMAIL_SENDER", ""),
    email_recipients=_parse_recipients(os.getenv("EMAIL_RECIPIENTS", "")),
    omg_shopify_domain=os.getenv("OMG_SHOPIFY_DOMAIN", "52922c-2.myshopify.com"),
    omg_shopify_admin_token=os.getenv("OMG_SHOPIFY_ADMIN_TOKEN", ""),
    omg_shopify_client_id=os.getenv("OMG_SHOPIFY_CLIENT_ID", ""),
    omg_shopify_client_secret=os.getenv("OMG_SHOPIFY_CLIENT_SECRET", ""),
    ngrok_domain=os.getenv("NGROK_DOMAIN", ""),
)
