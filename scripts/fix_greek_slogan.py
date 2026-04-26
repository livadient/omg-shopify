"""Strip Greek accents (tonos) from the σέξι μαδαφάκα slogan design + re-cache mockups."""
import asyncio
import json
import logging
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, "/project")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("fix_greek")

PROPOSAL_ID_SUBSTR = "Σέξι Μαδαφάκα"  # match proposal by name
NEW_TEXT = "ΣΕΞΙ\nΜΑΔΑΦΑΚΑ"  # uppercase, no tonos


def strip_accents(s: str) -> str:
    """Remove Greek tonos marks (combining acute accents)."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


async def main():
    from app.agents.approval import get_proposal
    from app.agents.design_creator import _precache_mockups
    from app.agents.image_client import generate_text_design

    # Find the proposal
    proposals_file = Path("/project/data/proposals.json")
    data = json.loads(proposals_file.read_text(encoding="utf-8"))
    proposals = data.get("proposals") if isinstance(data, dict) else data

    target = None
    for p in proposals:
        if p.get("data", {}).get("name", "").startswith("Σέξι"):
            target = p
    if not target:
        # Try by any containing Μαδαφάκα
        for p in proposals:
            if "μαδαφ" in p.get("data", {}).get("name", "").lower() or "μαδαφ" in p.get("data", {}).get("text_on_shirt", "").lower():
                target = p
        if not target:
            logger.error("Could not find Σέξι proposal; will list all:")
            for p in proposals[-10:]:
                logger.info(f"  id={p.get('id')} name={p.get('data', {}).get('name')}")
            return

    logger.info(f"Found proposal {target['id']}: {target['data']['name']}")
    old_image = target["data"].get("image_path", "")
    logger.info(f"Old image: {old_image}")

    # Regenerate without accents
    logger.info(f"Regenerating with text: {NEW_TEXT!r}")
    new_image_path = await generate_text_design(
        text=NEW_TEXT,
        style=target["data"].get("style", "bold modern"),
    )
    logger.info(f"New image: {new_image_path}")

    # Overwrite the old image file path so the existing proposal points to the fresh design
    if old_image:
        old_p = Path(old_image)
        # Copy new image over the old file so the proposal's stored path still works
        import shutil
        shutil.copy2(new_image_path, old_p)
        logger.info(f"Copied new design over {old_p}")

    # Re-cache mockups using the NEW image (at the OLD path, so cache filenames match the proposal)
    logger.info("Re-caching mockups...")
    cached = await _precache_mockups(str(old_image), target["data"].get("name", ""))
    target["data"]["cached_mockups"] = cached

    # Also update the image data if schema stores anything else
    target["data"]["text_on_shirt"] = NEW_TEXT

    # Save back
    proposals_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Proposal updated on disk")

    # Also update the stored name/description to reflect no-accent version
    ascii_form = strip_accents(target["data"].get("name", ""))
    logger.info(f"Stripped-accent name would be: {ascii_form}")


asyncio.run(main())
