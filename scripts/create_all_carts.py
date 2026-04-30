"""Generate cart permalinks for every active t-shirt × {male,female} ×
{front,back} = 4 carts per product. Email all links in one HTML
table.

Excludes the 5 astous tees (different TJ mapping schema). Uses each
product's design PNG from static/design_<handle>.png.

Time: ~60-90 min for 13 products (52 carts) with 2-way Playwright
parallelism. Designed to be run in the background.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.qstomizer_automation import customize_and_add_to_cart  # noqa: E402
from app.qstomizer_offsets import get_offsets  # noqa: E402
from app.agents.agent_email import send_agent_email  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DOMAIN = "52922c-2.myshopify.com"
TOKEN = os.environ["OMG_SHOPIFY_ADMIN_TOKEN"]
H = {"X-Shopify-Access-Token": TOKEN}

ASTOUS_EXCLUDE = {
    "astous-na-laloun-cyprus-female-tee",
    "astous-na-laloun-cyprus-male-tee",
    "astous-na-laloun-cyprus-female-limited-tee",
    "astous-na-laloun-cyprus-male-limited-tee",
    "astous-na-laloun-cyprus-unisex-tee",
}

# Per-product tee color (used by Qstomizer color swatch). Most text
# tees are White; normal-people + told-her are Black (white serif).
TEE_COLOR = {
    "normal-people-scare-me-tee": "Black",
    "told-her-shes-the-one-tee": "Black",
}


def discover() -> list[tuple[str, str, int]]:
    """Return [(handle, title, product_id)] for active t-shirts (excl astous)."""
    out: list[tuple[str, str, int]] = []
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"https://{DOMAIN}/admin/api/2024-01/products.json",
            headers=H, params={"status": "active", "limit": 250},
        )
        r.raise_for_status()
        for p in r.json()["products"]:
            h = p["handle"]
            if h in ASTOUS_EXCLUDE:
                continue
            titles = " ".join(v.get("title", "") for v in p.get("variants", []))
            if "Male" not in titles or "Female" not in titles:
                continue
            out.append((h, p["title"], p["id"]))
    return out


async def make_cart(
    handle: str,
    gender: str,
    placement: str,
) -> dict:
    """Create one cart and return result dict."""
    design_path = ROOT / "static" / f"design_{handle}.png"
    if not design_path.exists():
        return {"handle": handle, "gender": gender, "placement": placement,
                "error": "design PNG missing"}
    color = TEE_COLOR.get(handle, "White")
    size = "L" if gender == "male" else "M"
    v_off, h_off, pad = get_offsets(
        handle, gender, placement, design_path=str(design_path),
    )
    t0 = time.time()
    try:
        result = await customize_and_add_to_cart(
            product_type=gender, size=size, color=color,
            image_path=str(design_path), quantity=1, headless=True,
            placement=placement,
            vertical_offset=v_off,
            horizontal_offset=h_off,
            vertical_safety_pad_px=pad,
        )
        elapsed = time.time() - t0
        return {
            "handle": handle, "gender": gender, "placement": placement,
            "color": color, "size": size,
            "cart_url": result.get("checkout_url"),
            "mockup_url": result.get("mockup_url"),
            "elapsed": elapsed,
        }
    except Exception as e:
        elapsed = time.time() - t0
        logger.exception(f"[{handle}/{gender}/{placement}] failed")
        return {
            "handle": handle, "gender": gender, "placement": placement,
            "color": color, "size": size,
            "error": str(e), "elapsed": elapsed,
        }


def build_email_html(products: list[tuple[str, str, int]], results: list[dict]) -> str:
    """Build HTML email body with one row per cart."""
    by_handle: dict[str, dict] = {}
    for r in results:
        by_handle.setdefault(r["handle"], {})[(r["gender"], r["placement"])] = r

    rows = ""
    for handle, title, pid in products:
        cells = ""
        for combo in [("male", "front"), ("male", "back"), ("female", "front"), ("female", "back")]:
            r = by_handle.get(handle, {}).get(combo)
            if r and r.get("cart_url"):
                cells += (
                    f'<td style="padding:8px;border:1px solid #e5e7eb;">'
                    f'<a href="{r["cart_url"]}" style="color:#2563eb;font-weight:bold;">'
                    f'{combo[0][0].upper()}/{combo[1].title()}</a>'
                    f'<br><span style="font-size:11px;color:#6b7280;">{r.get("color","")} {r.get("size","")}</span>'
                    f"</td>"
                )
            elif r and r.get("error"):
                cells += (
                    f'<td style="padding:8px;border:1px solid #e5e7eb;background:#fef2f2;">'
                    f'<span style="color:#dc2626;font-size:11px;">{combo[0][0].upper()}/{combo[1].title()}<br>ERROR</span>'
                    f"</td>"
                )
            else:
                cells += '<td style="padding:8px;border:1px solid #e5e7eb;">—</td>'
        rows += (
            f'<tr><td style="padding:8px;border:1px solid #e5e7eb;font-weight:bold;font-size:13px;">'
            f'{title}<br><span style="color:#6b7280;font-weight:normal;font-size:11px;">{handle}</span>'
            f"</td>{cells}</tr>"
        )

    ok = sum(1 for r in results if r.get("cart_url"))
    err = sum(1 for r in results if r.get("error"))
    total_time = sum(r.get("elapsed", 0) for r in results)

    return f"""
    <div style="font-family:sans-serif;max-width:900px;margin:0 auto;">
        <div style="background:#2563eb;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Cart links — full t-shirt catalogue</h2>
            <p style="margin:4px 0 0;opacity:0.9;">
                One cart per (gender × placement) for every active t-shirt.
                <strong>{ok} OK</strong> | <strong>{err} errors</strong> |
                <strong>{total_time/60:.1f} min</strong> wall time.
            </p>
        </div>
        <div style="padding:16px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="background:#e5e7eb;">
                        <th style="padding:8px;text-align:left;">Product</th>
                        <th style="padding:8px;">Male / Front</th>
                        <th style="padding:8px;">Male / Back</th>
                        <th style="padding:8px;">Female / Front</th>
                        <th style="padding:8px;">Female / Back</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            <p style="margin-top:12px;color:#6b7280;font-size:12px;">
                Each link opens a TJ cart pre-filled with the design and Qstomizer
                properties. Click → checkout on TJ to ship.
            </p>
        </div>
    </div>
    """


async def main() -> None:
    products = discover()
    print(f"Discovered {len(products)} active t-shirts (excl astous)")
    for h, t, _ in products:
        print(f"  {h} — {t}")

    combos = [("male", "front"), ("male", "back"),
              ("female", "front"), ("female", "back")]
    plan = [(h, g, p) for h, _, _ in products for g, p in combos]

    # Resume mode — load existing JSON, skip already-OK entries.
    json_path = ROOT / "static" / "_carts_results.json"
    results: list[dict] = []
    done: set[tuple[str, str, str]] = set()
    if json_path.exists():
        try:
            results = json.loads(json_path.read_text())
            for r in results:
                # A real success has BOTH a checkout URL (not the bare
                # /cart) AND a mockup URL. Without the mockup the
                # Qstomizer customization didn't save and the cart is
                # dead — re-render it.
                cart = r.get("cart_url") or ""
                if cart and cart.rstrip("/") != "https://tshirtjunkies.co/cart" and r.get("mockup_url"):
                    done.add((r["handle"], r["gender"], r["placement"]))
            print(f"Resume: loaded {len(results)} prior results ({len(done)} OK).")
        except Exception as e:
            print(f"Could not load existing JSON ({e}); starting fresh.")
            results = []

    todo = [(h, g, p) for h, g, p in plan if (h, g, p) not in done]
    print(f"\nProcessing {len(todo)}/{len(plan)} carts SEQUENTIALLY (1-at-a-time + 30s sleep) to avoid TJ rate-limit...")

    for i, (h, g, p) in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {h}/{g}/{p}")
        r = await make_cart(h, g, p)
        # Replace any existing failed entry for this combo, otherwise append.
        results = [x for x in results if not (x.get("handle") == h
                                               and x.get("gender") == g
                                               and x.get("placement") == p)]
        results.append(r)
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        if i < len(todo):
            await asyncio.sleep(30)

    ok = [r for r in results if r.get("cart_url")]
    err = [r for r in results if r.get("error")]
    print(f"\n=== {len(ok)} OK / {len(err)} errors ===")
    for r in err:
        print(f"  [{r['handle']}/{r['gender']}/{r['placement']}] {r.get('error')}")

    # Raw results were saved incrementally during the loop above.
    raw = ROOT / "static" / "_carts_results.json"
    print(f"  raw results -> {raw}")

    html = build_email_html(products, results)
    await send_agent_email(
        subject=f"[Carts] Full catalogue — {len(ok)}/{len(results)} carts ready",
        html_body=html,
    )
    print("Email sent.")


if __name__ == "__main__":
    asyncio.run(main())
