# Agent 1: SEO Blog Writer (Olive)

## Purpose

Generates SEO-optimized blog posts about OMG t-shirts and publishes them to the Shopify store. Targets Cyprus, Greece, and European markets with keyword-rich content to drive organic traffic.

## Schedule

**Twice per week:** Tuesday and Friday at 05:00 Cyprus time (Europe/Nicosia)

## Flow

```
1. Scheduler triggers blog_writer.generate_proposal()
2. Fetch current OMG products via Shopify Admin API
3. Fetch existing blog articles (for dedup / context)
4. Call Claude API with SEO blog writing prompt
5. Claude returns: title, meta_description, body_html, tags
6. Save proposal to data/proposals.json (status: "pending")
7. Email user with blog preview + Approve/Reject links
8. User clicks Approve link
9. Publish article via Shopify Blog Article API
```

## Shopify Blog API

Requires OAuth scopes: `read_content, write_content` (must be added to existing scopes and re-authorized).

### Endpoints Used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/admin/api/2024-01/blogs.json` | List blogs (get blog_id) |
| GET | `/admin/api/2024-01/blogs/{id}/articles.json` | List articles (context/dedup) |
| POST | `/admin/api/2024-01/blogs/{id}/articles.json` | Create/publish article |

### Article Payload

```json
{
  "article": {
    "title": "Top 5 Greek-Inspired T-Shirt Designs for Summer 2026",
    "body_html": "<h2>...</h2><p>...</p>",
    "tags": "t-shirts, greek fashion, summer 2026, cyprus",
    "metafields_global_title_tag": "Greek T-Shirt Designs | OMG",
    "metafields_global_description_tag": "Discover the hottest..."
  }
}
```

## LLM Prompt Strategy

The system prompt includes:
- Brand identity: OMG (omg.com.cy), Cyprus-based t-shirt brand
- Current products: handles, titles, descriptions, prices
- Target markets: Cyprus → Greece → Europe (progressive expansion)
- SEO requirements: keyword density, H2/H3 structure, meta description length
- Tone: casual, trendy, Mediterranean lifestyle
- Word count: 800-1500 words
- Language: English (primary), with Greek/Cypriot cultural references

The agent rotates through topic angles:
- Product spotlights
- Seasonal content (summer collections, holiday gifting)
- Greek/Cypriot culture + fashion
- T-shirt styling guides
- Behind-the-scenes / brand story

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agents/blog/proposals` | List all proposals (pending/approved/rejected) |
| POST | `/agents/blog/generate` | Manually trigger a new blog proposal |
| GET | `/agents/blog/approve/{id}?token=...` | Approve and publish (shows confirmation first) |
| GET | `/agents/blog/reject/{id}?token=...` | Reject proposal |
| GET | `/agents/blog/preview/{id}` | View full blog post HTML |

## Email Preview Format

```
Subject: [OMG Blog] New post ready for review: "Top 5 Greek-Inspired..."

Preview:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Title: Top 5 Greek-Inspired T-Shirt Designs for Summer 2026
Tags: t-shirts, greek fashion, summer 2026
Meta: Discover the hottest Greek-inspired...
Word Count: 1,247

[First 300 characters of body...]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✅ APPROVE]  [❌ REJECT]  [👁 FULL PREVIEW]
```

## Configuration

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `OMG_SHOPIFY_BLOG_ID` | Target blog ID for publishing |

## Module

**File:** `app/agents/blog_writer.py`

**Dependencies:** `app/agents/llm_client.py`, `app/agents/approval.py`, `app/shopify_blog.py`, `app/email_service.py`
