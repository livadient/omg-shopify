"""Build a local HTML preview of every completed cart in
static/_carts_results.json so you can scan progress + click into
TJ checkout pages without waiting for the email."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

JSON_PATH = ROOT / "static" / "_carts_results.json"
OUT = ROOT / "static" / "_carts_preview.html"


def main() -> None:
    if not JSON_PATH.exists():
        sys.exit(f"missing {JSON_PATH}")
    data = json.loads(JSON_PATH.read_text())

    by_handle: dict[str, dict] = {}
    for r in data:
        by_handle.setdefault(r["handle"], {})[(r["gender"], r["placement"])] = r

    ok = sum(1 for r in data if r.get("cart_url"))
    err = sum(1 for r in data if r.get("error"))

    rows = ""
    for handle in sorted(by_handle):
        cells = ""
        for combo in [("male", "front"), ("male", "back"),
                      ("female", "front"), ("female", "back")]:
            r = by_handle[handle].get(combo)
            if r and r.get("cart_url"):
                cells += f"""
                <td style="padding:4px;border:1px solid #e5e7eb;width:200px;">
                    <a href="{r['cart_url']}" target="_blank">
                        <img src="{r['mockup_url']}" style="width:100%;display:block;" alt="{combo[0]} {combo[1]}">
                    </a>
                    <div style="font-size:10px;color:#6b7280;text-align:center;">
                        {combo[0]}/{combo[1]} · {r.get('color','')} {r.get('size','')}
                    </div>
                </td>"""
            elif r and r.get("error"):
                cells += f"""
                <td style="padding:8px;border:1px solid #e5e7eb;background:#fef2f2;color:#dc2626;font-size:11px;text-align:center;width:200px;">
                    {combo[0]}/{combo[1]}<br>ERROR
                </td>"""
            else:
                cells += '<td style="padding:8px;border:1px solid #e5e7eb;color:#9ca3af;text-align:center;width:200px;">—</td>'
        rows += (
            f'<tr><td style="padding:8px;border:1px solid #e5e7eb;font-weight:bold;font-size:13px;vertical-align:top;width:200px;">'
            f'{handle}'
            f"</td>{cells}</tr>"
        )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Cart preview ({ok}/{len(data)})</title></head>
<body style="font-family:sans-serif;margin:20px;">
<h1>Cart preview ({ok} OK / {err} errors / {len(data)} attempted of 48)</h1>
<p>Click any tee image to open the TJ cart permalink.</p>
<table style="border-collapse:collapse;">
<thead>
<tr style="background:#e5e7eb;">
<th style="padding:8px;">Product</th>
<th style="padding:8px;">Male / Front</th>
<th style="padding:8px;">Male / Back</th>
<th style="padding:8px;">Female / Front</th>
<th style="padding:8px;">Female / Back</th>
</tr>
</thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""

    OUT.write_text(html, encoding="utf-8")
    print(f"saved -> {OUT}")
    print(f"open in browser: file:///{OUT.as_posix()}")


if __name__ == "__main__":
    main()
