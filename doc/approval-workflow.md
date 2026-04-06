# Approval Workflow

## Overview

Token-based approval system that gates agent actions requiring human review before execution. Agents create proposals with data and a secret token; humans approve or reject via URL links in emails.

**File:** `app/agents/approval.py`

## Storage

Proposals are stored in `data/proposals.json` at the project root. The `data/` directory is created automatically if it does not exist.

## Proposal Structure

```json
{
  "id": "a1b2c3d4",
  "token": "550e8400-e29b-41d4-a716-446655440000",
  "agent": "design_creator",
  "status": "pending",
  "created_at": "2026-04-06T10:30:00+00:00",
  "data": { ... },
  "updated_at": "2026-04-06T11:00:00+00:00"
}
```

| Field | Description |
|-------|-------------|
| `id` | Short identifier -- first 8 characters of a UUID4 |
| `token` | Full UUID4 -- secret used for URL-based approval |
| `agent` | Agent name (e.g., `blog_writer`, `design_creator`) |
| `status` | `pending`, `approved`, or `rejected` |
| `created_at` | ISO 8601 timestamp (UTC) |
| `data` | Agent-specific payload (article content, design details, etc.) |
| `updated_at` | Set when status changes |

## Statuses

```
pending  ──┬──>  approved
            └──>  rejected
```

Only `pending` proposals can be approved or rejected. Once a status is set, the proposal cannot be changed again.

## Token Validation

`validate_token(proposal_id, token)` checks:
1. Proposal with the given ID exists
2. Token matches the proposal's token
3. Status is `pending`

Returns the proposal dict if all checks pass, `None` otherwise.

## Approval URLs

Built by `approval_url(proposal_id, token, action)`:

```
{server_base_url}/agents/{agent}/{action}/{proposal_id}?token={token}
```

Examples:
```
http://40.81.137.240:8080/agents/blog_writer/approve/a1b2c3d4?token=550e8400-...
http://40.81.137.240:8080/agents/design_creator/reject/a1b2c3d4?token=550e8400-...
```

These URLs are included in agent proposal emails so reviewers can approve or reject with a single click.

## API Functions

| Function | Description |
|----------|-------------|
| `create_proposal(agent, data)` | Create a new proposal. Returns the full proposal dict with ID and token. |
| `get_proposal(proposal_id)` | Look up a proposal by ID. Returns `None` if not found. |
| `list_proposals(agent, status)` | List proposals with optional agent and/or status filters. |
| `validate_token(proposal_id, token)` | Validate a token for approval/rejection. Returns proposal or `None`. |
| `update_status(proposal_id, status)` | Set status to `approved` or `rejected`. Sets `updated_at`. |
| `approval_url(proposal_id, token, action)` | Build an approval/rejection URL for email links. |

## Agent Usage

### Blog Writer

Creates proposals with article content (title, body HTML, tags, SEO metadata). On approval, the article is published to the OMG Shopify blog.

### Design Creator

Creates proposals with design details (concept, image paths, product data). On approval, the product is created on Shopify, mappings are set up, and mockup images are uploaded.

### Ranking Advisor

Does **not** use the approval workflow. It generates advisory reports that are sent directly via email without requiring approval.

## Endpoints

The approval/rejection endpoints are registered in `main.py` for each agent that uses the workflow:

- `GET /agents/blog_writer/approve/{proposal_id}?token=...`
- `GET /agents/blog_writer/reject/{proposal_id}?token=...`
- `GET /agents/design_creator/approve/{proposal_id}?token=...`
- `GET /agents/design_creator/reject/{proposal_id}?token=...`
