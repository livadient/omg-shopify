# AI Agents Setup Guide

## Prerequisites

- Existing OMG Shopify Python service running (see main README)
- Anthropic API key (for Claude)
- OpenAI API key (for DALL-E 3 image generation)
- `rembg` with `onnxruntime` for background removal (u2net model downloads ~170MB on first run)

## 1. Install New Dependencies

```bash
.venv/Scripts/pip install anthropic openai apscheduler rembg onnxruntime
```

Or update from requirements.txt:
```bash
.venv/Scripts/pip install -r requirements.txt
```

## 2. Configure Environment Variables

Add to your `.env` file:

```env
# AI Agent Configuration
ANTHROPIC_API_KEY=sk-ant-...          # Get from console.anthropic.com
OPENAI_API_KEY=sk-...                 # Get from platform.openai.com
OMG_SHOPIFY_BLOG_ID=                  # See step 3 below
AGENT_TIMEZONE=Europe/Nicosia         # Scheduler timezone
```

## 3. Get the Shopify Blog ID

The blog writer agent needs a blog to publish to. Either:

**Option A: Use existing blog**
```bash
curl -s -H "X-Shopify-Access-Token: YOUR_TOKEN" \
  https://52922c-2.myshopify.com/admin/api/2024-01/blogs.json | python -m json.tool
```
Copy the `id` field from the response.

**Option B: Create a new blog**
Go to OMG Shopify Admin > Online Store > Blog posts > Manage blogs > Add blog.

Set `OMG_SHOPIFY_BLOG_ID` in `.env` to the blog's numeric ID.

## 4. Re-authorize Shopify OAuth (Required for Blog Agent)

The blog writer needs `read_content` and `write_content` scopes which aren't in the current token.

1. Start the server: `.venv/Scripts/python app/main.py`
2. Visit `http://localhost:8080/shopify-auth`
3. Authorize the app in Shopify
4. Copy the new token and update `OMG_SHOPIFY_ADMIN_TOKEN` in `.env`

## 5. Create Data Directory

```bash
mkdir data
```

This directory stores agent proposals. In Docker, it's mounted as a volume for persistence.

## 6. Docker Configuration

Update `docker-compose.yml` volumes:
```yaml
volumes:
  - ./product_mappings.json:/project/product_mappings.json
  - ./static:/project/static      # whole directory (for new designs)
  - ./data:/project/data           # proposal storage
  - u2net_cache:/root/.u2net       # rembg model cache (persists across rebuilds)
```

## 7. Verify Setup

Start the server and test each agent manually:

```bash
# Test Ranking Advisor (Agent 3)
curl -X POST http://localhost:8080/agents/ranking/generate

# Test Blog Writer (Agent 1)
curl -X POST http://localhost:8080/agents/blog/generate

# Test Design Creator (Agent 2)
curl -X POST http://localhost:8080/agents/design/research
```

Check your email for the generated content.

## Agent Schedules

Once running, agents execute automatically:

| Agent | Schedule | Time (Cyprus) |
|-------|----------|---------------|
| Design Creator | Mon-Fri | 04:00 |
| ~~SEO Optimizer~~ | ~~Mon-Fri~~ | ~~04:30~~ | **DISABLED** |
| Blog Writer | Tue, Fri | 05:00 |
| Ranking Advisor | Mon-Fri | 07:00 |

## Costs

| Service | Estimated Monthly Cost |
|---------|----------------------|
| Claude API (Anthropic) | $15-40 (includes web search for trend research) |
| DALL-E 3 (OpenAI) | $20-40 (5 designs/day, Mon-Fri) |
| **Total** | **$35-80** |

## Troubleshooting

### "No blog found" error
- Verify `OMG_SHOPIFY_BLOG_ID` is set correctly in `.env`
- Ensure OAuth token has `read_content` scope (re-authorize if needed)

### DALL-E 3 returns error
- Check `OPENAI_API_KEY` is valid
- Ensure account has billing enabled at platform.openai.com

### Emails not arriving
- Check `SMTP_*` and `EMAIL_*` settings in `.env`
- Check spam folder for approval emails

### Scheduler not running
- Agents only run on schedule when the server is running
- Use manual trigger endpoints for testing
- Check logs for scheduler startup confirmation
