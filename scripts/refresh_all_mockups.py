"""Replace every live TJ mockup on the 12 active t-shirts with the
fresh mockup_url from the recent cart batch (static/_carts_results.json).

The cart batch already rendered all 48 mockups with the per-product
offsets from get_offsets() (incl. graphic-vs-text auto-detection
for image tees), so this just downloads each URL and uploads it as
the new mockup, preserving variant_ids and position. Old images are
saved to static/_replaced_mockups_backup/ before replacement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from app.shopify_product_creator import upload_product_image  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DOMAIN = "52922c-2.myshopify.com"
TOKEN = os.environ["OMG_SHOPIFY_ADMIN_TOKEN"]
H = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

CARTS_JSON = ROOT / "static" / "_carts_results.json"
LOCAL_OUT = ROOT / "static" / "_refreshed_mockups"
BACKUP = ROOT / "static" / "_replaced_mockups_backup"


def find_mockup_image(p: dict, gender: str, placement: str) -> dict | None:
    """Find the live image whose filename matches <gender>_<placement>.
    Use leading underscore to disambiguate male_back vs female_back."""
    needle = f"_{gender}_{placement}"  # e.g. "_male_front"
    for img in p.get("images", []):
        fn = img["src"].rsplit("/", 1)[-1].split("?")[0].lower()
        # Match TJ mockup files (mockup_cache_*) OR the freshly-pushed
        # short names (e.g. female_front_<hash>.png).
        if needle in fn or fn.startswith(f"{gender}_{placement}"):
            return img
    return None


async def replace_one(c_async, c_sync, handle: str, gender: str, placement: str, mockup_url: str) -> None:
    if not mockup_url:
        print(f"  [{handle}/{gender}/{placement}] no mockup_url — skip")
        return

    out_dir = LOCAL_OUT / handle
    out_dir.mkdir(parents=True, exist_ok=True)
    new_local = out_dir / f"{gender}_{placement}.png"
    rd = await c_async.get(mockup_url, timeout=60)
    rd.raise_for_status()
    new_local.write_bytes(rd.content)

    r = c_sync.get(
        f"https://{DOMAIN}/admin/api/2024-01/products.json",
        headers=H, params={"handle": handle},
    )
    r.raise_for_status()
    p = r.json()["products"][0]
    pid = p["id"]
    target = find_mockup_image(p, gender, placement)
    if not target:
        print(f"  [{handle}/{gender}/{placement}] no live mockup found")
        return

    old_pos = target["position"]
    old_variant_ids = list(target.get("variant_ids") or [])
    old_id = target["id"]
    old_src = target["src"]

    # Backup the live one we're about to replace
    BACKUP.mkdir(parents=True, exist_ok=True)
    short = handle.split("-")[0]
    bpath = BACKUP / f"{short}_{gender}_{placement}_old.png"
    if not bpath.exists():
        rb = c_sync.get(old_src)
        if rb.status_code == 200:
            bpath.write_bytes(rb.content)

    new_img = await upload_product_image(
        product_id=pid, image_path=new_local,
        alt=f"{gender} {placement}",
        variant_ids=old_variant_ids,
    )
    new_id = new_img["id"]
    rd2 = c_sync.delete(
        f"https://{DOMAIN}/admin/api/2024-01/products/{pid}/images/{old_id}.json",
        headers=H,
    )
    if rd2.status_code not in (200, 204):
        print(f"  [{handle}/{gender}/{placement}] delete old failed: {rd2.status_code}")
        return

    rp = c_sync.put(
        f"https://{DOMAIN}/admin/api/2024-01/products/{pid}/images/{new_id}.json",
        headers=H,
        json={"image": {"id": new_id, "position": old_pos, "variant_ids": old_variant_ids}},
    )
    if rp.status_code != 200:
        print(f"  [{handle}/{gender}/{placement}] reposition failed: {rp.status_code}")
        return
    upd = rp.json()["image"]
    print(f"  [{handle}/{gender}/{placement}] OK new_id={new_id} pos={upd['position']} vars={len(upd.get('variant_ids') or [])}")


async def main() -> None:
    if not CARTS_JSON.exists():
        sys.exit(f"missing {CARTS_JSON}")
    results = json.loads(CARTS_JSON.read_text())

    work = [r for r in results if not r.get("error") and r.get("mockup_url")]
    print(f"Will replace {len(work)} live mockups.")

    async with httpx.AsyncClient() as c_async:
        with httpx.Client(timeout=60) as c_sync:
            for w in work:
                try:
                    await replace_one(c_async, c_sync, w["handle"], w["gender"], w["placement"], w["mockup_url"])
                except Exception as e:
                    print(f"  [{w['handle']}/{w['gender']}/{w['placement']}] ERROR: {e}")
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
