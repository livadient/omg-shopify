"""Replace two separate male/female Limited Edition products with a single unified product."""
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.shopify_product_creator import (
    create_product, create_mappings_for_product,
    upload_product_image, fetch_mockup_from_qstomizer, download_image,
    _admin_url, _headers,
)

import httpx

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

OLD_PRODUCT_IDS = [15900033646972, 15900033876348]  # male, female
OLD_EU_HANDLES = [
    "astous-va-laloun-graphic-tee-male-eu-edition",
    "astous-va-laloun-graphic-tee-female-eu-edition",
]


async def main():
    # Step 1: Create the new unified product
    print("=" * 60)
    print("STEP 1: Creating new unified product...")
    print("=" * 60)

    product = await create_product(
        title="Astous na Laloun - Limited Edition Tee",
        body_html=(
            "<p>Wear your Cypriot pride with the iconic <strong>\"Αστούς να Λαλούν\"</strong> design — "
            "a vibrant celebration of Cyprus, featuring the island's silhouette with olive branches "
            "and bold Greek typography. Limited Edition, premium quality.</p>"
        ),
        tags="astous na laloun, cyprus, limited edition, graphic tee, greek, cypriot",
        published=True,
    )

    product_id = product["id"]
    handle = product["handle"]
    print(f"  Created product: {product_id}")
    print(f"  Handle: {handle}")
    print(f"  URL: https://omg.com.cy/products/{handle}")
    print(f"  Variants: {len(product.get('variants', []))}")

    # Step 2: Fetch mockups from Qstomizer and upload images
    print()
    print("=" * 60)
    print("STEP 2: Fetching mockups and uploading images...")
    print("=" * 60)

    design_path = str(STATIC_DIR / "front_design.png")
    proposals_dir = STATIC_DIR / "proposals"
    proposals_dir.mkdir(exist_ok=True)

    for ptype, size, label in [("male", "L", "Male"), ("female", "M", "Female")]:
        print(f"  Fetching {label} mockup from Qstomizer...")
        mockup_url = await fetch_mockup_from_qstomizer(design_path, ptype, size)
        if mockup_url:
            mockup_path = proposals_dir / f"mockup_{handle}_{ptype}.png"
            await download_image(mockup_url, mockup_path)
            await upload_product_image(product_id, mockup_path, alt=f"{label} T-Shirt Mockup")
            print(f"  Uploaded {label} mockup")
        else:
            print(f"  WARNING: Failed to get {label} mockup")

    # Upload the design artwork as the last image
    design_file = STATIC_DIR / "front_design.png"
    if design_file.exists():
        await upload_product_image(product_id, design_file, alt="Astous na Laloun Design Artwork")
        print("  Uploaded design artwork")

    # Step 3: Create mappings to TShirtJunkies
    print()
    print("=" * 60)
    print("STEP 3: Creating product mappings...")
    print("=" * 60)

    mappings = await create_mappings_for_product(
        omg_product=product,
        design_image="front_design.png",
    )
    print(f"  Created {len(mappings)} mappings")
    for m in mappings:
        print(f"    {m['source_handle']} → {m['target_handle']} ({len(m['variants'])} variants)")

    # Step 4: Remove old EU edition mappings from product_mappings.json
    print()
    print("=" * 60)
    print("STEP 4: Removing old EU edition mappings...")
    print("=" * 60)

    mappings_file = Path(__file__).resolve().parent.parent / "product_mappings.json"
    data = json.loads(mappings_file.read_text(encoding="utf-8"))
    before_count = len(data["mappings"])
    data["mappings"] = [
        m for m in data["mappings"]
        if m["source_handle"] not in OLD_EU_HANDLES
    ]
    after_count = len(data["mappings"])
    removed = before_count - after_count
    mappings_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Removed {removed} old EU edition mappings")

    # Step 5: Archive the two old products
    print()
    print("=" * 60)
    print("STEP 5: Archiving old products...")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        for old_id in OLD_PRODUCT_IDS:
            resp = await client.put(
                _admin_url(f"products/{old_id}.json"),
                headers=_headers(),
                json={"product": {"id": old_id, "status": "archived"}},
                timeout=30,
            )
            if resp.status_code < 400:
                print(f"  Archived product {old_id}")
            else:
                print(f"  WARNING: Failed to archive {old_id}: {resp.status_code} {resp.text[:200]}")

    # Done
    print()
    print("=" * 60)
    print("DONE!")
    print(f"  New product: https://omg.com.cy/products/{handle}")
    print(f"  Admin: https://admin.shopify.com/store/52922c-2/products/{product_id}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
