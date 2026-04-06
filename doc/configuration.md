# Configuration

## Overview

All settings are loaded from environment variables via `python-dotenv`. The `.env` file at the project root is loaded on import. Configuration is centralized in `app/config.py` using a Pydantic `BaseModel`.

**File:** `app/config.py`

## Settings Class

```python
class Settings(BaseModel):
    # Shopify Webhook
    shopify_webhook_secret: str
    tshirtjunkies_base_url: str
    host: str
    port: int

    # SMTP / Email
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    email_sender: str
    email_recipients: list[str]

    # OMG Shopify Admin API
    omg_shopify_domain: str
    omg_shopify_admin_token: str
    omg_shopify_client_id: str
    omg_shopify_client_secret: str

    # Infrastructure
    ngrok_domain: str
    server_base_url: str

    # AI Agents
    anthropic_api_key: str
    openai_api_key: str
    omg_shopify_blog_id: str
    agent_timezone: str
```

The `settings` singleton is created at module level and can be imported from anywhere:
```python
from app.config import settings
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| **Shopify & Webhook** | | |
| `SHOPIFY_WEBHOOK_SECRET` | `""` | Webhook HMAC verification secret |
| `TSHIRTJUNKIES_BASE_URL` | `https://tshirtjunkies.co` | Target store base URL |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| **SMTP / Email** | | |
| `SMTP_HOST` | `""` | SMTP server hostname (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | `587` | SMTP port (587 for STARTTLS) |
| `SMTP_USERNAME` | `""` | SMTP login username |
| `SMTP_PASSWORD` | `""` | SMTP login password (use app password for Gmail) |
| `EMAIL_SENDER` | `""` | From address for all notifications |
| `EMAIL_RECIPIENTS` | `""` | Comma-separated list of recipient email addresses |
| **OMG Shopify Admin API** | | |
| `OMG_SHOPIFY_DOMAIN` | `52922c-2.myshopify.com` | OMG store myshopify domain |
| `OMG_SHOPIFY_ADMIN_TOKEN` | `""` | Admin API access token (obtained via OAuth) |
| `OMG_SHOPIFY_CLIENT_ID` | `""` | Shopify app client ID (for OAuth flow) |
| `OMG_SHOPIFY_CLIENT_SECRET` | `""` | Shopify app client secret (for OAuth flow) |
| **Infrastructure** | | |
| `NGROK_DOMAIN` | `""` | Fixed ngrok domain (e.g., `myapp.ngrok-free.dev`) |
| `SERVER_BASE_URL` | `http://40.81.137.240:8080` | Public base URL for links in emails and approval URLs |
| **AI Agents** | | |
| `ANTHROPIC_API_KEY` | `""` | Anthropic Claude API key (for LLM client) |
| `OPENAI_API_KEY` | `""` | OpenAI API key (for DALL-E 3 image generation) |
| `OMG_SHOPIFY_BLOG_ID` | `""` | Shopify blog ID for the blog writer agent |
| `AGENT_TIMEZONE` | `Europe/Nicosia` | Timezone for agent scheduling (APScheduler) |

## Email Recipients Parsing

`EMAIL_RECIPIENTS` is parsed from a comma-separated string into a list:
```python
def _parse_recipients(raw: str) -> list[str]:
    return [r.strip() for r in raw.split(",") if r.strip()]
```

Example: `EMAIL_RECIPIENTS=alice@example.com, bob@example.com`

## Docker Usage

In Docker deployment, environment variables are loaded from `.env` via `docker-compose.yml`:

```yaml
services:
  omg-shopify:
    env_file: .env
```

## .env.example

A template file (`.env.example`) is provided at the project root with all variables listed. Copy it to `.env` and fill in the values:

```bash
cp .env.example .env
```

## Notes

- Empty string defaults (`""`) mean the feature is disabled when not configured. The service degrades gracefully (e.g., emails are skipped if SMTP is not set, agents are skipped if API keys are missing).
- `SERVER_BASE_URL` is used for approval URLs in emails and manual order fallback links. Must be reachable by email recipients.
- `AGENT_TIMEZONE` controls when scheduled agents run. Set to `Europe/Nicosia` (UTC+2/+3) by default since the store operates in Cyprus.
- `PORT` in the config defaults to `8000`, but the deployment typically uses `8080` (set via environment variable). The OAuth redirect URI is hardcoded to port 8080.
