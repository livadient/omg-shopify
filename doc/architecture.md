# AI Agents Architecture

## Overview

Three AI agents integrate into the existing FastAPI service to automate content creation, product design, and SEO optimization for the OMG t-shirt store.

**Decision: Build in-app (not Make.com)** — all Shopify API connections already exist, Playwright automation is deeply integrated, and Make.com would add cost ($9-16/month) on top of the same AI API fees (~$15-50/month).

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Agent 1:     │  │  Agent 2:     │  │  Agent 3:     │      │
│  │  SEO Blog     │  │  Design       │  │  Ranking      │      │
│  │  Writer       │  │  Creator      │  │  Advisor      │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │              │
│  ┌──────▼──────────────────▼──────────────────▼───────┐     │
│  │              Shared Infrastructure                  │     │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐     │     │
│  │  │ LLM Client │ │ Image Gen  │ │ Scheduler  │     │     │
│  │  │ (Claude)   │ │ (DALL-E 3) │ │(APScheduler│     │     │
│  │  └────────────┘ └────────────┘ └────────────┘     │     │
│  │  ┌────────────┐ ┌────────────┐                    │     │
│  │  │ Approval   │ │ Email      │                    │     │
│  │  │ Workflow   │ │ Service    │                    │     │
│  │  └────────────┘ └────────────┘                    │     │
│  └────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌────────────────────────────────────────────────────┐     │
│  │              Existing Services                      │     │
│  │  Shopify Admin API │ Playwright │ Cart Client       │     │
│  │  Product Mapper    │ Email SMTP │ Qstomizer Auto    │     │
│  └────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
         │                    │                  │
         ▼                    ▼                  ▼
   OMG Shopify Store    TShirtJunkies     User Email
   (Admin API)          (Qstomizer)       (Approval)
```

## Common Agent Pattern

All agents follow the same lifecycle:

```
Scheduler (cron) ──► Agent generates proposal
                         │
                         ▼
                    Save to data/proposals.json
                         │
                         ▼
                    Email user with preview
                    + Approve/Reject links
                         │
                         ▼
                    User clicks link
                         │
                    ┌────┴────┐
                    ▼         ▼
              Approve      Reject
              (execute)    (discard)
```

Exception: Agent 3 (Ranking Advisor) skips approval — it's advisory only, sending daily email recommendations directly.

## File Structure

```
app/
  agents/
    __init__.py              # Package init
    llm_client.py            # Anthropic SDK wrapper
    image_client.py          # OpenAI DALL-E 3 wrapper
    scheduler.py             # APScheduler cron setup
    approval.py              # Proposal storage + token-based approval
    blog_writer.py           # Agent 1: SEO Blog Writer
    design_creator.py        # Agent 2: Trend Research & Design
    ranking_advisor.py       # Agent 3: Google Ranking Advisor
  shopify_blog.py            # Shopify Blog Article Admin API
  shopify_product_creator.py # Shopify Product creation Admin API
data/
  proposals.json             # Proposal storage (persisted via Docker volume)
doc/
  architecture.md            # This file
  agent1-blog-writer.md      # Agent 1 specification
  agent2-design-creator.md   # Agent 2 specification
  agent3-ranking-advisor.md  # Agent 3 specification
  setup-guide.md             # Setup and configuration guide
  api-endpoints.md           # New API endpoint reference
```

## External APIs

| Service | Purpose | Cost Estimate |
|---------|---------|---------------|
| Claude API (Anthropic) | Content generation for all 3 agents | ~$10-30/month |
| DALL-E 3 (OpenAI) | T-shirt design image generation | ~$5-15/month |
| Gmail SMTP | Email notifications + approval links | Free (existing) |
| Shopify Admin API | Blog posts, product creation, order data | Free (existing) |

## Data Flow Summary

| Agent | Input | AI Processing | Output | Action on Approval |
|-------|-------|---------------|--------|-------------------|
| Blog Writer | Product catalog, existing articles | Claude generates SEO blog post | HTML article + meta | Publish to Shopify blog |
| Design Creator | Trend research context | Claude ideates + DALL-E 3 renders | Design PNG + product spec | Create Shopify product + mapping |
| Ranking Advisor | Products, articles, market focus | Claude analyzes + recommends | Email report | N/A (advisory) |
