"""Agent: Argus — Nightly design QA checker.

Runs every mapped design through Qstomizer, compares the mockup against
the original design file using Claude vision, and emails a report.
Catches Qstomizer _customorderid collisions and broken design files
before customers hit them.
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
MAPPINGS_FILE = Path(__file__).resolve().parent.parent.parent / "product_mappings.json"


async def run_design_qa() -> dict:
    """Run QA checks on all mapped designs and send email report."""
    try:
        return await _run_design_qa_impl()
    except Exception as e:
        logger.exception("Argus design QA failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Argus", e)
        raise


async def _run_design_qa_impl() -> dict:
    """Internal implementation."""
    from app.qstomizer_automation import customize_and_add_to_cart
    from app.main import verify_mockup_matches_design

    logger.info("Argus: starting nightly design QA")

    mappings = json.loads(MAPPINGS_FILE.read_text(encoding="utf-8"))

    # Deduplicate by design_image — one test per unique design file
    seen = {}
    for m in mappings.get("mappings", []):
        img = m.get("design_image", "")
        if img and img not in seen:
            target = m.get("target_handle", "")
            ptype = "male" if "classic" in target else "female"
            seen[img] = {
                "handle": m["source_handle"],
                "design_image": img,
                "product_type": ptype,
                "size": "L" if ptype == "male" else "M",
            }

    tests = list(seen.values())
    logger.info(f"Argus: {len(tests)} unique designs to verify")

    results = []
    for i, test in enumerate(tests, 1):
        handle = test["handle"]
        design_img = test["design_image"]
        ptype = test["product_type"]
        size = test["size"]
        design_path = STATIC_DIR / design_img

        logger.info(f"[{i}/{len(tests)}] {handle} ({ptype} {size})")

        if not design_path.exists():
            logger.error(f"  SKIP: {design_img} not found")
            results.append({
                "handle": handle, "design": design_img,
                "status": "SKIP", "details": "design file not found",
            })
            continue

        t0 = time.time()
        try:
            result = await customize_and_add_to_cart(
                product_type=ptype,
                size=size,
                color="White",
                image_path=str(design_path),
                quantity=1,
                headless=True,
            )
            elapsed = time.time() - t0
            mockup_url = result.get("mockup_url")

            if not mockup_url:
                logger.warning(f"  WARN: no mockup url ({elapsed:.0f}s)")
                results.append({
                    "handle": handle, "design": design_img,
                    "status": "WARN", "details": "no mockup URL returned",
                    "time": elapsed,
                })
                continue

            verification = await verify_mockup_matches_design(mockup_url, design_path)
            match = verification.get("match", True)
            details = verification.get("details", "")
            status = "PASS" if match else "FAIL"

            logger.info(f"  {status} ({elapsed:.0f}s) — {details}")
            results.append({
                "handle": handle, "design": design_img,
                "status": status, "details": details,
                "time": elapsed, "mockup_url": mockup_url,
            })

        except Exception as e:
            elapsed = time.time() - t0
            logger.error(f"  ERROR ({elapsed:.0f}s): {e}")
            results.append({
                "handle": handle, "design": design_img,
                "status": "ERROR", "details": str(e),
                "time": elapsed,
            })

    # Send email report
    await _send_qa_report(results)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    total_time = sum(r.get("time", 0) for r in results)

    logger.info(
        f"Argus: QA complete — PASS: {passed} | FAIL: {failed} | "
        f"ERROR: {errors} | Total: {total_time:.0f}s"
    )

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_time": total_time,
        "results": results,
    }


async def _send_qa_report(results: list[dict]) -> None:
    """Send email with QA results."""
    from app.agents.agent_email import send_agent_email

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    warned = sum(1 for r in results if r["status"] == "WARN")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    total = len(results)

    has_issues = failed > 0 or errors > 0

    # Build results table rows
    rows = ""
    for r in results:
        status = r["status"]
        handle = r["handle"]
        details = r.get("details", "")
        time_s = f"{r.get('time', 0):.0f}s" if r.get("time") else ""

        if status == "PASS":
            badge = '<span style="background:#059669;color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">PASS</span>'
        elif status == "FAIL":
            badge = '<span style="background:#dc2626;color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">FAIL</span>'
        elif status == "ERROR":
            badge = '<span style="background:#d97706;color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">ERROR</span>'
        else:
            badge = f'<span style="background:#6b7280;color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{status}</span>'

        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{badge}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-weight:bold;">{handle}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#4b5563;">{details[:120]}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#9ca3af;">{time_s}</td>
        </tr>"""

    header_bg = "#dc2626" if has_issues else "#d97706"
    header_msg = (
        f"Found {failed + errors} issue(s) that need attention!"
        if has_issues
        else f"All {passed} designs verified — looking good!"
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:700px;margin:0 auto;">
        <div style="background:{header_bg};color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Argus here — nightly QA report</h2>
            <p style="margin:4px 0 0;opacity:0.9;">{header_msg}</p>
        </div>
        <div style="padding:16px;background:#f9fafb;">
            <div style="display:flex;gap:16px;margin-bottom:16px;font-size:14px;">
                <span><strong>{passed}</strong> passed</span>
                <span><strong style="color:#dc2626;">{failed}</strong> failed</span>
                <span><strong style="color:#d97706;">{errors}</strong> errors</span>
                <span><strong>{warned + skipped}</strong> skipped</span>
                <span>| <strong>{total}</strong> total</span>
            </div>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#e5e7eb;">
                        <th style="padding:8px;text-align:left;width:60px;">Status</th>
                        <th style="padding:8px;text-align:left;">Design</th>
                        <th style="padding:8px;text-align:left;">Details</th>
                        <th style="padding:8px;text-align:left;width:50px;">Time</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;">
            Inspected by Argus, your all-seeing QA watchman
        </div>
    </div>
    """

    subject_prefix = "ISSUES FOUND" if has_issues else "All clear"
    await send_agent_email(
        subject=f"[Argus] {subject_prefix} — {passed}/{total} designs verified",
        html_body=html,
    )
