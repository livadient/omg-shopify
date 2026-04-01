"""Parse OMG order confirmation email text into structured order data."""
import re


def parse_order_email(text: str) -> dict:
    """Parse pasted OMG order email text into order data.

    Expected format:
        Order summary

        Astous na Laloun Graphic Tee Male — EU Edition × 1
        M
        €30,00
        ...
        Shipping address
        Vangelis Livadiotis
        7 Michalaki Zampa
        2109 Nicosia
        Cyprus

    Returns dict with: items (list), shipping (dict), total (str).
    """
    # Normalize unicode spaces and characters
    text = text.replace("\xa0", " ").replace("\u2014", "—")
    lines = [l.strip() for l in text.strip().splitlines()]

    items = []
    shipping = {}
    total = ""

    # --- Parse line items ---
    # Pattern: "Product Name × Qty" followed by size on next line
    i = 0
    while i < len(lines):
        match = re.match(r"^(.+?)[\s\xa0]*[×xX\*][\s\xa0]*(\d+)\s*$", lines[i])
        if match:
            title = match.group(1).strip()
            quantity = int(match.group(2))

            # Next line is the size
            size = ""
            if i + 1 < len(lines) and lines[i + 1] in (
                "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL",
            ):
                size = lines[i + 1]
                i += 1

            # Determine product type from title
            title_lower = title.lower()
            if "female" in title_lower or "women" in title_lower:
                product_type = "female"
            else:
                product_type = "male"

            items.append({
                "title": title,
                "variant_title": size,
                "quantity": quantity,
                "product_type": product_type,
            })
        i += 1

    # --- Parse total ---
    for i, line in enumerate(lines):
        if line.lower() == "total" and i + 1 < len(lines):
            # Next non-empty line is the total value
            for j in range(i + 1, min(i + 3, len(lines))):
                if lines[j] and "€" in lines[j]:
                    total = lines[j]
                    break
            break

    # --- Parse shipping address ---
    # Find "Shipping address" header, then read the block after it
    for i, line in enumerate(lines):
        if line.lower() == "shipping address" and i + 1 < len(lines):
            addr_lines = []
            j = i + 1
            # Collect lines until we hit another section header or end
            while j < len(lines):
                if lines[j].lower() in (
                    "billing address", "payment method", "shipping method",
                    "customer information", "",
                ):
                    if lines[j] == "":
                        # Skip blank lines within the address block
                        j += 1
                        continue
                    break
                addr_lines.append(lines[j])
                j += 1

            if len(addr_lines) >= 1:
                # First line: full name
                name_parts = addr_lines[0].split(None, 1)
                shipping["first_name"] = name_parts[0] if name_parts else ""
                shipping["last_name"] = name_parts[1] if len(name_parts) > 1 else ""

            if len(addr_lines) >= 2:
                shipping["address1"] = addr_lines[1]

            if len(addr_lines) >= 3:
                # "2109 Nicosia" or "Nicosia 2109" — zip + city
                city_zip = addr_lines[2]
                zip_match = re.match(r"^(\d{4,5})\s+(.+)$", city_zip)
                if zip_match:
                    shipping["zip"] = zip_match.group(1)
                    shipping["city"] = zip_match.group(2)
                else:
                    zip_match2 = re.match(r"^(.+?)\s+(\d{4,5})$", city_zip)
                    if zip_match2:
                        shipping["city"] = zip_match2.group(1)
                        shipping["zip"] = zip_match2.group(2)
                    else:
                        shipping["city"] = city_zip

            if len(addr_lines) >= 4:
                shipping["country"] = addr_lines[3]

            # Map country name to code
            shipping["country_code"] = _country_to_code(
                shipping.get("country", "")
            )
            break

    return {
        "items": items,
        "shipping": shipping,
        "total": total,
    }


COUNTRY_CODES = {
    "cyprus": "CY",
    "greece": "GR",
    "united kingdom": "GB",
    "uk": "GB",
    "germany": "DE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
    "netherlands": "NL",
    "belgium": "BE",
    "austria": "AT",
    "portugal": "PT",
    "ireland": "IE",
    "sweden": "SE",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "czech republic": "CZ",
    "romania": "RO",
    "bulgaria": "BG",
    "hungary": "HU",
    "united states": "US",
    "usa": "US",
    "canada": "CA",
    "australia": "AU",
}


def _country_to_code(country: str) -> str:
    name = country.strip().lower()
    return COUNTRY_CODES.get(name, country[:2].upper() if country else "")
