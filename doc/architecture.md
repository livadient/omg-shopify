# AI Agents Architecture

## Overview

Four AI agents integrate into the existing FastAPI service to automate content creation, product design, translations, and SEO optimization for the OMG t-shirt store. Each agent has a name and personality shown in email communications. A fifth agent (Sphinx, SEO optimizer) exists but is currently **disabled** — its tasks are executed manually based on Atlas' recommendations instead.

**Decision: Build in-app (not Make.com)** — all Shopify API connections already exist, Playwright automation is deeply integrated, and Make.com would add cost ($9-16/month) on top of the same AI API fees (~$15-50/month).

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                        │
│                                                              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐│
│  │ Olive:     │ │ Mango:     │ │ Atlas:     │ │ Hermes:    ││
│  │ Blog       │ │ Design     │ │ Ranking    │ │ Translation││
│  │ Writer     │ │ Creator    │ │ Advisor    │ │ Checker    ││
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘│
│         │                  │                  │              │
│  ┌──────────────────────────────────────────────────┐      │
│  │              SEO Management                       │      │
│  │  Handle fixer │ Homepage SEO │ Collections        │      │
│  └──────────────────────────────────────────────────┘      │
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

Exceptions: Agent 3 (Atlas / Ranking Advisor) skips approval -- it's advisory only, sending daily email recommendations directly. Agent 4 (Hermes / Translation Checker) also skips approval -- it registers translations immediately and sends a summary report.

## File Structure

```
app/
  agents/
    __init__.py              # Package init
    llm_client.py            # Anthropic SDK wrapper
    image_client.py          # OpenAI DALL-E 3 wrapper
    scheduler.py             # APScheduler cron setup
    approval.py              # Proposal storage + token-based approval
    blog_writer.py           # Agent "Olive": SEO Blog Writer
    design_creator.py        # Agent "Mango": Trend Research & Design
    ranking_advisor.py       # Agent "Atlas": Google Ranking Advisor
    translation_checker.py   # Agent "Hermes": Translation Checker (EN→GR)
    agent_email.py           # Agent-specific email formatting
  shopify_blog.py            # Shopify Blog Article Admin API
  shopify_product_creator.py # Shopify Product creation Admin API
  shopify_translations.py    # Shopify GraphQL Translations API
data/
  proposals.json             # Proposal storage (persisted via Docker volume)
doc/
  architecture.md            # This file
  agent1-blog-writer.md      # Agent 1 specification
  agent2-design-creator.md   # Agent 2 specification
  agent3-ranking-advisor.md  # Agent 3 specification
  agent4-translation-checker.md # Agent 4 specification
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

| Agent (Name) | Input | AI Processing | Output | Action on Approval |
|--------------|-------|---------------|--------|-------------------|
| Blog Writer (Olive) | Product catalog, existing articles | Claude generates SEO blog post | HTML article + meta | Publish to Shopify blog |
| Design Creator (Mango) | Web search trends, market data | Claude ideates + DALL-E 3 renders | Design PNG + cached mockups | Create Shopify product + mapping |
| Ranking Advisor (Atlas) | Products, articles, market focus | Claude analyzes + recommends | Email report | N/A (advisory) |
| Translation Checker (Hermes) | Shopify translatable resources | Claude translates EN→GR | Registered translations | Immediate (no approval) |
| SEO Management (Sphinx) | Product catalog, store metadata | Automated optimization | Fixed handles, meta tags, collections | **DISABLED** — run manually via `python -m app.seo_management all` |

## SEO Management

SEO optimization tasks run as scheduled jobs and can also be triggered manually via API endpoints:

- **Handle fixer:** Scans for duplicate product handles and fixes them
- **Homepage SEO:** Updates homepage meta title and description tags
- **Collections:** Creates Cyprus-specific collections for local SEO

These tasks use the Shopify Admin API directly (no LLM needed).
