# AI Agent API Endpoints

All agent endpoints are prefixed with `/agents/`.

## Agent 1: Blog Writer

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/agents/blog/generate` | Manually trigger a new blog proposal | None |
| GET | `/agents/blog/proposals` | List all proposals (pending/approved/rejected) | None |
| GET | `/agents/blog/preview/{id}` | View full blog post HTML | None |
| GET | `/agents/blog/approve/{id}?token={secret}` | Approve and publish blog post | Token |
| GET | `/agents/blog/reject/{id}?token={secret}` | Reject blog proposal | Token |

### Generate Request
```bash
curl -X POST http://localhost:8080/agents/blog/generate
```

### Response
```json
{
  "proposal_id": "abc123",
  "status": "pending",
  "title": "Top 5 Greek-Inspired T-Shirt Designs for Summer 2026",
  "message": "Email sent with preview and approval links"
}
```

### Approve (from email link)
```
GET /agents/blog/approve/abc123?token=xyz789
```
Returns HTML confirmation page. On confirm, publishes the article to Shopify.

---

## Agent 2: Design Creator

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/agents/design/research` | Trigger trend research + design generation | None |
| GET | `/agents/design/proposals` | List all design proposals | None |
| GET | `/agents/design/preview/{id}` | View design image + product details | None |
| GET | `/agents/design/approve/{id}?token={secret}` | Approve → create product + mapping | Token |
| GET | `/agents/design/reject/{id}?token={secret}` | Reject design | Token |

### Research Request
```bash
curl -X POST http://localhost:8080/agents/design/research
```

### Response
```json
{
  "proposals": [
    {
      "proposal_id": "def456",
      "status": "pending",
      "concept": "Mediterranean Sunset",
      "style": "Minimalist vector illustration",
      "image_url": "/static/proposals/def456.png"
    }
  ],
  "message": "5 designs generated. Email sent for review."
}
```

### Approve (from email link)
```
GET /agents/design/approve/def456?token=xyz789
```
On confirm:
1. Creates product on OMG Shopify store
2. Uploads design image
3. Creates variant mapping to TShirtJunkies
4. Saves design PNG for Playwright automation

---

## Agent 3: Ranking Advisor

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/agents/ranking/generate` | Manually trigger today's report | None |
| GET | `/agents/ranking/history` | View past reports (last 30 days) | None |

### Generate Request
```bash
curl -X POST http://localhost:8080/agents/ranking/generate
```

### Response
```json
{
  "status": "sent",
  "market_focus": "CY",
  "recommendations_count": 3,
  "message": "Daily ranking report sent via email"
}
```

### History Response
```json
{
  "reports": [
    {
      "date": "2026-04-05",
      "market_focus": "EU",
      "top_actions": ["Add schema markup", "..."],
      "sent_at": "2026-04-05T07:00:02Z"
    }
  ]
}
```

---

## SEO Management

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/seo/fix-handles` | Fix duplicate product handles | None |
| POST | `/seo/homepage` | Update homepage SEO meta tags | None |
| POST | `/seo/collections` | Create Cyprus-specific collections | None |
| POST | `/seo/all` | Run all SEO tasks | None |

### Fix Handles
```bash
curl -X POST http://localhost:8080/seo/fix-handles
```
Scans products for duplicate handles and fixes them to ensure unique, SEO-friendly URLs.

### Run All SEO Tasks
```bash
curl -X POST http://localhost:8080/seo/all
```
Runs all SEO optimization tasks in sequence: fix handles, update homepage meta tags, create collections.

---

## Common Response Models

### Proposal
```json
{
  "id": "uuid",
  "agent": "blog|design|ranking",
  "status": "pending|approved|rejected",
  "created_at": "2026-04-05T10:00:00Z",
  "data": {}
}
```

### Error
```json
{
  "detail": "Error description"
}
```

## Authentication

Agent endpoints are unauthenticated (same as existing endpoints). Approval actions require a `token` query parameter that is generated per-proposal and included in the email links. Tokens are single-use.

## Rate Limits

Manual trigger endpoints have no built-in rate limiting, but each call incurs AI API costs:
- Blog generation: ~$0.02-0.05 per call (Claude)
- Design research: ~$0.25-0.50 per call (Claude with web search + 5x DALL-E 3)
- Ranking report: ~$0.01-0.03 per call (Claude)
