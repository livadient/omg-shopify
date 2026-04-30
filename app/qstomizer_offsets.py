"""Single source of truth for Qstomizer placement offsets per product.

The default offsets push slogan/text prints to upper-chest. Image
designs are auto-detected by aspect ratio (h/w >= 0.4 = more square)
and given centered placement instead, so illustrations sit inside the
print-area Rect rather than bleeding above it like small text.
Per-product overrides handle wide single-line slogans (clip on right
with default h-shift), tall 4-line designs (clip the top line above
the collar), and bold typography that fills the print area.

Used by:
- app/main.py cart endpoints (webhook + manual)
- app/agents/design_creator.py Mango _precache_mockups
- scripts/create_all_carts.py
"""
from __future__ import annotations


# Default offsets per (gender, placement). All placements default to
# v=-0.5 with a -15px bleed above the print-area Rect so tall designs
# can land at upper-chest like dont-tempt-me's small (43px) design
# does at v=-0.25 pad=4 — visually matching across products of
# different design heights.
DEFAULTS: dict[tuple[str, str], tuple[float, float, int]] = {
    # (gender, placement): (v_offset, h_offset, safety_pad_px)
    ("male", "front"):   (-0.5, 0.0, -45),  # extra bleed → slightly higher than other placements
    ("male", "back"):    (-0.5, 0.0, -30),
    ("female", "front"): (-0.5, 0.05, -30),  # +0.05 h-shift fixes left-bias
    ("female", "back"):  (-0.5, 0.0, -30),
}

# Per-product overrides. Keyed by (handle, gender, placement).
# Only override what differs from the default.
OVERRIDES: dict[tuple[str, str, str], dict] = {
    # === i-dont-get-drunk: wide single-line slogan (~33 chars). The
    # default +0.05 h-shift on female_front clips "awesome" on right.
    ("i-dont-get-drunk-i-get-awesome-tee", "female", "front"): {
        "h_offset": 0.02,
    },

    # === dont-tempt-me: bold Impact 2-line. Already at upper-chest
    # with v=-0.25 pad=4 because design_h is small (43px). Default
    # v=-0.5 pad=-15 would bleed too far. Pin its current look.
    ("dont-tempt-me-ill-say-yes-tee", "male", "front"): {
        "v_offset": -0.25, "h_offset": 0.0, "safety_pad": 4,
    },
    ("dont-tempt-me-ill-say-yes-tee", "male", "back"): {
        "v_offset": -0.25, "h_offset": 0.0, "safety_pad": 4,
    },
    ("dont-tempt-me-ill-say-yes-tee", "female", "front"): {
        "v_offset": -0.25, "h_offset": 0.0, "safety_pad": 4,
    },
    ("dont-tempt-me-ill-say-yes-tee", "female", "back"): {
        "v_offset": -0.25, "h_offset": 0.0, "safety_pad": 4,
    },

    # === normal-people: tall 4-line "NORMAL/PEOPLE/SCARE/ME" — design_h
    # is large (166px); aggressive defaults clip top line above collar.
    # Centered placement (v=0, pad=4) keeps all 4 lines visible.
    ("normal-people-scare-me-tee", "male", "front"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },
    ("normal-people-scare-me-tee", "male", "back"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },
    ("normal-people-scare-me-tee", "female", "front"): {
        "v_offset": 0.10, "h_offset": 0.0, "safety_pad": 4,
    },
    ("normal-people-scare-me-tee", "female", "back"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },

    # === told-her: tall 4-line "TOLD HER / SHE'S THE ONE / NOT THE /
    # ONLY ONE" — same constraints as normal-people. Centered placement.
    ("told-her-shes-the-one-tee", "male", "front"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },
    ("told-her-shes-the-one-tee", "male", "back"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },
    ("told-her-shes-the-one-tee", "female", "front"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },
    ("told-her-shes-the-one-tee", "female", "back"): {
        "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
    },

}


# Aspect threshold (height / width) to classify a design as graphic
# vs text. Text slogans are wide and short (aspect < 0.4); full-color
# illustrations are more square (aspect >= 0.4). Same threshold used
# in app/agents/marketing_pipeline.py.
GRAPHIC_ASPECT_THRESHOLD = 0.4

# Centered placement applied to graphic/illustration designs that
# have no explicit per-product override. Big illustrations want to
# sit in the print-area Rect, not bleed above it like small text.
GRAPHIC_DEFAULT: dict[str, float | int] = {
    "v_offset": 0.0, "h_offset": 0.0, "safety_pad": 4,
}


def _is_graphic_design(design_path: str) -> bool:
    """Return True if the design PNG is a graphic/illustration (more
    square) rather than a text slogan (wide and short).

    Uses the alpha bbox of the actual content, not the canvas size —
    text designs typically sit on a transparent square canvas, so the
    file aspect is meaningless. Falls back to False on any error so
    we never break the cart flow over aspect detection."""
    try:
        from PIL import Image
        with Image.open(design_path) as im:
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            bbox = im.split()[-1].getbbox()  # alpha-channel bbox
            if not bbox:
                return False
            x0, y0, x1, y1 = bbox
            w, h = x1 - x0, y1 - y0
        if w <= 0:
            return False
        return (h / w) >= GRAPHIC_ASPECT_THRESHOLD
    except Exception:
        return False


def get_offsets(
    handle: str | None,
    gender: str,
    placement: str,
    design_path: str | None = None,
) -> tuple[float, float, int]:
    """Return (vertical_offset, horizontal_offset, safety_pad_px) for
    a given product + gender + placement.

    `handle` may be None when the call site doesn't know the product
    (e.g. legacy fallback paths) — defaults are returned in that case.

    `design_path`, when provided, enables graphic-vs-text auto-detection
    so any image/illustration tee gets centered placement (v=0 pad=4)
    instead of the upper-chest text defaults. Per-product overrides
    always win over the auto-detection.
    """
    base = DEFAULTS.get((gender, placement), (0.0, 0.0, 4))
    v_off, h_off, pad = base

    # Aspect-based fallback: graphic designs centered, text designs
    # keep upper-chest defaults. Skipped when an explicit override
    # exists for this (handle, gender, placement).
    has_override = bool(handle) and (handle, gender, placement) in OVERRIDES
    if not has_override and design_path and _is_graphic_design(design_path):
        v_off = float(GRAPHIC_DEFAULT["v_offset"])
        h_off = float(GRAPHIC_DEFAULT["h_offset"])
        pad = int(GRAPHIC_DEFAULT["safety_pad"])

    if handle:
        override = OVERRIDES.get((handle, gender, placement), {})
        if "v_offset" in override:
            v_off = override["v_offset"]
        if "h_offset" in override:
            h_off = override["h_offset"]
        if "safety_pad" in override:
            pad = override["safety_pad"]
    return v_off, h_off, pad
