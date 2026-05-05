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
    # Vangelis's personal email — used as the buyer-side checkout email on
    # TJ (the dropship workflow has him as the actual payer, not the OMG
    # customer) and as the sole recipient for TEST-* webhook runs so test
    # traffic doesn't notify everyone.
    test_email_recipient: str = "livadient@gmail.com"
    tj_checkout_email: str = "livadient@gmail.com"
    # Discount code applied at TJ checkout when rebuilding the cart
    tj_discount_code: str = "OHMANGOESSHOP"
    # OMG Shopify Admin API
    omg_shopify_domain: str = "52922c-2.myshopify.com"
    omg_shopify_admin_token: str = ""
    omg_shopify_client_id: str = ""
    omg_shopify_client_secret: str = ""
    ngrok_domain: str = ""
    server_base_url: str = "http://40.81.137.240:8080"
    # AI Agents
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    omg_shopify_blog_id: str = ""
    agent_timezone: str = "Europe/Nicosia"
    # Google APIs
    google_service_account_file: str = ""
    google_search_console_site: str = ""
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_customer_id: str = ""


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
    test_email_recipient=os.getenv("TEST_EMAIL_RECIPIENT", "livadient@gmail.com"),
    tj_checkout_email=os.getenv("TJ_CHECKOUT_EMAIL", "livadient@gmail.com"),
    tj_discount_code=os.getenv("TJ_DISCOUNT_CODE", "OHMANGOESSHOP"),
    omg_shopify_domain=os.getenv("OMG_SHOPIFY_DOMAIN", "52922c-2.myshopify.com"),
    omg_shopify_admin_token=os.getenv("OMG_SHOPIFY_ADMIN_TOKEN", ""),
    omg_shopify_client_id=os.getenv("OMG_SHOPIFY_CLIENT_ID", ""),
    omg_shopify_client_secret=os.getenv("OMG_SHOPIFY_CLIENT_SECRET", ""),
    ngrok_domain=os.getenv("NGROK_DOMAIN", ""),
    server_base_url=os.getenv("SERVER_BASE_URL", "http://40.81.137.240:8080"),
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
    openai_api_key=os.getenv("OPENAI_API_KEY", ""),
    omg_shopify_blog_id=os.getenv("OMG_SHOPIFY_BLOG_ID", ""),
    agent_timezone=os.getenv("AGENT_TIMEZONE", "Europe/Nicosia"),
    google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
    google_search_console_site=os.getenv("GOOGLE_SEARCH_CONSOLE_SITE", ""),
    google_ads_developer_token=os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
    google_ads_client_id=os.getenv("GOOGLE_ADS_CLIENT_ID", ""),
    google_ads_client_secret=os.getenv("GOOGLE_ADS_CLIENT_SECRET", ""),
    google_ads_refresh_token=os.getenv("GOOGLE_ADS_REFRESH_TOKEN", ""),
    google_ads_customer_id=os.getenv("GOOGLE_ADS_CUSTOMER_ID", ""),
)
