"""Inspect Atlas' most recent campaign proposal to verify if it's based on
real Google Ads Keyword Planner data (Basic access granted) or LLM fallback."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    data = json.loads(Path("/project/data/proposals.json").read_text(encoding="utf-8"))
    proposals = data.get("proposals") if isinstance(data, dict) else data

    cyprus = None
    for p in reversed(proposals):
        if p.get("type") == "campaign":
            d = p.get("data", {})
            if "Cyprus" in d.get("campaign_name", "") or d.get("market") == "CY":
                cyprus = p
                break

    if not cyprus:
        print("No Cyprus campaign proposal found")
        return

    d = cyprus.get("data", {})
    print(f"Proposal ID: {cyprus.get('id')}")
    print(f"Status:      {cyprus.get('status')}")
    print(f"Created:     {cyprus.get('created_at')}")
    print(f"Name:        {d.get('campaign_name')}")
    print(f"Market:      {d.get('market')}")
    print(f"Budget:      EUR {d.get('daily_budget_eur')}")
    print(f"Max CPC:     EUR {d.get('max_cpc_eur')}")
    print(f"Landing:     {d.get('final_url')}")
    print(f"Keywords ({len(d.get('keywords', []))}):")
    for kw in d.get("keywords", [])[:10]:
        # Print everything we can find on each keyword
        print(f"  {kw}")

    # Also check if there's a separate keyword_metrics field
    print(f"\nReasoning excerpt:\n  {d.get('reasoning', '')[:500]}")
    print()
    has_real_metrics = False
    for kw in d.get("keywords", []):
        if isinstance(kw, dict):
            if "monthly_searches" in kw or "search_volume" in kw or "cpc_low" in kw or "cpc_high" in kw:
                has_real_metrics = True
                break
    print(f"Real Keyword Planner metrics in keywords: {has_real_metrics}")


main()
