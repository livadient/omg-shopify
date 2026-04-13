"""Inspect the Greek slogan product on Shopify to see where accents remain."""
import asyncio
import os
import httpx


async def main():
    t = os.environ["OMG_SHOPIFY_ADMIN_TOKEN"]
    headers = {"X-Shopify-Access-Token": t}
    base = "https://52922c-2.myshopify.com/admin/api/2024-01"

    async with httpx.AsyncClient(timeout=30) as c:
        # Look up by handle substring (URL-decoded)
        r = await c.get(
            f"{base}/products.json?handle=\u03c3\u03ad\u03be\u03b9-\u03bc\u03b1\u03b4\u03b1\u03c6\u03ac\u03ba\u03b1-greek-slogan-tee",
            headers=headers,
        )
        products = r.json().get("products", [])
        if not products:
            print("NOT FOUND by handle, trying broader search...")
            # Get all tee products and find by title
            r2 = await c.get(f"{base}/products.json?limit=250", headers=headers)
            for p in r2.json().get("products", []):
                t2 = p.get("title", "")
                h = p.get("handle", "")
                if "madaf" in h.lower() or "madaf" in t2.lower() or "\u03bc\u03b1\u03b4\u03b1\u03c6" in t2:
                    products = [p]
                    break

        if not products:
            print("Still not found")
            return

        p = products[0]
        print(f"ID:     {p['id']}")
        print(f"Title:  {p.get('title')!r}")
        print(f"Handle: {p.get('handle')!r}")
        print(f"Status: {p.get('status')}")
        print(f"Tags:   {p.get('tags')}")
        print(f"Images ({len(p.get('images', []))}):")
        for img in p.get("images", []):
            print(f"  id={img.get('id')} alt={img.get('alt')!r} src={img.get('src', '')[:80]}...")
        print(f"Body:   {p.get('body_html', '')[:200]}")


asyncio.run(main())
