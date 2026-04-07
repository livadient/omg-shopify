"""Agent: Translation Checker — finds untranslated/outdated content and translates to Greek via Claude."""
import logging

from app.agents import llm_client
from app.agents.agent_email import send_agent_email
from app.shopify_translations import (
    ensure_locale_enabled,
    find_untranslated,
    register_translations,
)

logger = logging.getLogger(__name__)

TARGET_LOCALE = "el"

TRANSLATION_SYSTEM_PROMPT = """You are a professional English-to-Greek translator for an online t-shirt store called OMG (omg.com.cy) based in Cyprus.

Rules:
- Translate naturally into modern Greek as spoken in Cyprus/Greece
- Keep brand names untranslated: OMG, Oh Mangoes, TShirtJunkies
- Keep product design names/slogans untranslated if they are part of the design (e.g. "Astous na Laloun" stays as is)
- For e-commerce terms use standard Greek: "Add to cart" = "Προσθήκη στο καλάθι", "Check out" = "Ολοκλήρωση αγοράς", etc.
- "Size" = "Μέγεθος", "Gender" = "Φύλο", "Male" = "Ανδρικό", "Female" = "Γυναικείο"
- "Sale" = "Έκπτωση", "Sold out" = "Εξαντλήθηκε"
- Preserve HTML tags exactly as they are
- Preserve any Liquid template tags ({{ }}, {% %}) exactly as they are
- Do not translate URLs, email addresses, or code
- Keep measurements, numbers, and currency symbols as-is
- For SEO meta descriptions, keep them compelling and within similar character count

Output ONLY the translated text, nothing else. No quotes, no explanation."""


async def check_and_fix_translations() -> dict:
    """Main entry point: find untranslated content, translate via Claude, register, and email report."""
    try:
        return await _check_and_fix_impl()
    except Exception as e:
        logger.exception("Translation Checker failed")
        from app.agents.agent_email import send_error_email
        await send_error_email("Translation Checker", e)
        raise


async def _check_and_fix_impl() -> dict:
    logger.info("Translation Checker: starting")

    # Ensure Greek locale is enabled
    locale_ok = await ensure_locale_enabled(TARGET_LOCALE)
    if not locale_ok:
        logger.error("Could not enable Greek locale — aborting")
        return {"error": "Failed to enable Greek locale"}

    # Find all untranslated/outdated content
    untranslated = await find_untranslated(locale=TARGET_LOCALE)

    if not untranslated:
        logger.info("Translation Checker: everything is translated!")
        return {"translated": 0, "errors": 0}

    total_fields = sum(len(r["fields"]) for r in untranslated)
    logger.info(f"Found {len(untranslated)} resources with {total_fields} fields needing translation")

    # Translate and register in batches per resource
    results = []
    error_count = 0

    for resource in untranslated:
        resource_id = resource["resource_id"]
        resource_type = resource["resource_type"]
        fields = resource["fields"]

        # Build translation request — batch all fields for this resource into one Claude call
        source_texts = {f["key"]: f["value"] for f in fields}
        translations_to_register = []

        try:
            translated = await _translate_batch(source_texts)

            for field in fields:
                key = field["key"]
                greek_text = translated.get(key)
                if not greek_text:
                    logger.warning(f"No translation returned for {resource_id} / {key}")
                    error_count += 1
                    continue

                translations_to_register.append({
                    "locale": TARGET_LOCALE,
                    "key": key,
                    "value": greek_text,
                    "translatableContentDigest": field["digest"],
                })

            if translations_to_register:
                reg_result = await register_translations(resource_id, translations_to_register)
                user_errors = reg_result.get("userErrors", [])
                if user_errors:
                    error_count += len(user_errors)

                results.append({
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "fields": [
                        {"key": f["key"], "english": source_texts[f["key"]], "greek": translated.get(f["key"], "ERROR")}
                        for f in fields
                    ],
                    "registered": len(reg_result.get("translations", [])),
                    "errors": user_errors,
                })

        except Exception as e:
            logger.error(f"Failed to translate {resource_id}: {e}")
            error_count += 1
            results.append({
                "resource_id": resource_id,
                "resource_type": resource_type,
                "fields": [{"key": f["key"], "english": source_texts.get(f["key"], ""), "greek": "ERROR"} for f in fields],
                "registered": 0,
                "errors": [str(e)],
            })

    # Send email report
    total_registered = sum(r["registered"] for r in results)
    await _send_report_email(results, total_registered, error_count)

    logger.info(f"Translation Checker done: {total_registered} fields translated, {error_count} errors")
    return {"translated": total_registered, "errors": error_count, "details": results}


async def _translate_batch(source_texts: dict[str, str]) -> dict[str, str]:
    """Translate a batch of key-value pairs from English to Greek using Claude.

    Returns dict of key -> Greek translation.
    """
    if len(source_texts) == 1:
        # Single field — simple translation
        key, value = next(iter(source_texts.items()))
        greek = await llm_client.generate(
            system_prompt=TRANSLATION_SYSTEM_PROMPT,
            user_prompt=f"Translate to Greek:\n\n{value}",
            max_tokens=2000,
            temperature=0.3,
        )
        return {key: greek.strip()}

    # Multiple fields — use JSON format for efficiency
    import json

    fields_text = json.dumps(source_texts, ensure_ascii=False, indent=2)
    prompt = (
        "Translate each value from English to Greek. "
        "Return a JSON object with the same keys but Greek values.\n\n"
        f"```json\n{fields_text}\n```"
    )

    response = await llm_client.generate(
        system_prompt=TRANSLATION_SYSTEM_PROMPT,
        user_prompt=prompt,
        max_tokens=4000,
        temperature=0.3,
    )

    # Parse JSON response
    text = response.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        result = json.loads(text.strip())
        return result
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse batch translation, falling back to individual")
        # Fallback: translate one by one
        results = {}
        for key, value in source_texts.items():
            greek = await llm_client.generate(
                system_prompt=TRANSLATION_SYSTEM_PROMPT,
                user_prompt=f"Translate to Greek:\n\n{value}",
                max_tokens=2000,
                temperature=0.3,
            )
            results[key] = greek.strip()
        return results


async def _send_report_email(results: list[dict], total_registered: int, error_count: int) -> None:
    """Send email report of translation activity."""
    if not results:
        return

    rows_html = ""
    for r in results:
        for f in r["fields"]:
            english = f["english"][:80] + "..." if len(f.get("english", "")) > 80 else f.get("english", "")
            greek = f["greek"][:80] + "..." if len(f.get("greek", "")) > 80 else f.get("greek", "")
            status_color = "#059669" if f["greek"] != "ERROR" else "#dc2626"
            status = "OK" if f["greek"] != "ERROR" else "FAILED"

            # Extract readable resource name from GID
            resource_name = r["resource_id"].split("/")[-1]
            rtype = r["resource_type"].lower().replace("_", " ")

            rows_html += f"""
            <tr>
                <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">{rtype}<br><span style="color:#9ca3af;font-size:11px;">{resource_name}</span></td>
                <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">{f['key']}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">{english}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">{greek}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;color:{status_color};font-weight:bold;">{status}</td>
            </tr>
            """

    html = f"""
    <div style="font-family:sans-serif;max-width:900px;margin:0 auto;">
        <div style="background:#2563eb;color:white;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">Translation Checker Report</h2>
            <p style="margin:4px 0 0;opacity:0.9;">{total_registered} fields translated, {error_count} errors</p>
        </div>
        <div style="padding:16px;background:#f9fafb;">
            <table style="width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;">
                <thead>
                    <tr style="background:#f3f4f6;">
                        <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;">Resource</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;">Field</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;">English</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;">Greek</th>
                        <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;">Status</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>
        <div style="padding:12px;text-align:center;color:#9ca3af;font-size:12px;">
            Generated by OMG Translation Checker
        </div>
    </div>
    """

    await send_agent_email(
        subject=f"[OMG Translations] {total_registered} fields translated, {error_count} errors",
        html_body=html,
    )
