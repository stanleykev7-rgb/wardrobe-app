"""
Builds a simple stylized "paper doll" mannequin SVG for the outfit
suggestion page - a minimal front-facing figure with each body zone
filled in the actual color of the garment picked for it. This is a
deliberately abstract illustration (not a photorealistic person), which
keeps it simple, fast, and avoids any concerns around generating images
of real people.

Zones with no pick get a dashed, unfilled placeholder (styled via the
.mannequin-empty-zone CSS class, which uses currentColor so it adapts to
light/dark mode) rather than being left out, so the figure always reads
as a complete person.
"""

from color_utils import color_to_hex

SKIN_TONE = "#E3C7A8"


def _zone_fill(picks: dict, zone: str) -> tuple:
    """Returns (fill_attr, css_class) for a zone - a real color if there's
    a pick, or a themeable dashed placeholder if not."""
    item = picks.get(zone)
    if item:
        return color_to_hex(item.get("color", "")), ""
    return "none", "mannequin-empty-zone"


def mannequin_svg(picks: dict) -> str:
    top_fill, top_class = _zone_fill(picks, "top")
    bottom_fill, bottom_class = _zone_fill(picks, "bottom")
    feet_fill, feet_class = _zone_fill(picks, "feet")
    head_fill, head_class = _zone_fill(picks, "head")

    # Head accessory only draws as a filled cap when there's an actual
    # head-zone pick; otherwise just a thin dashed brim outline.
    head_accessory = (
        f'<path d="M40 20 Q60 2 80 20 L80 26 L40 26 Z" fill="{head_fill}" class="{head_class}"/>'
    )

    return f"""
<svg class="mannequin" width="120" height="230" viewBox="0 0 120 230" fill="none" xmlns="http://www.w3.org/2000/svg">
  <!-- head -->
  <circle cx="60" cy="34" r="18" fill="{SKIN_TONE}"/>
  <!-- head accessory (hat), only meaningfully filled if a head item is picked -->
  {head_accessory}
  <!-- torso (top) -->
  <path d="M34 56 Q60 48 86 56 L92 118 Q60 128 28 118 Z" fill="{top_fill}" stroke-width="2" class="{top_class}"/>
  <!-- arms -->
  <rect x="18" y="60" width="16" height="52" rx="8" fill="{top_fill}" class="{top_class}"/>
  <rect x="86" y="60" width="16" height="52" rx="8" fill="{top_fill}" class="{top_class}"/>
  <!-- legs (bottom) -->
  <rect x="34" y="118" width="22" height="62" rx="8" fill="{bottom_fill}" class="{bottom_class}"/>
  <rect x="64" y="118" width="22" height="62" rx="8" fill="{bottom_fill}" class="{bottom_class}"/>
  <!-- feet -->
  <ellipse cx="45" cy="188" rx="14" ry="8" fill="{feet_fill}" class="{feet_class}"/>
  <ellipse cx="75" cy="188" rx="14" ry="8" fill="{feet_fill}" class="{feet_class}"/>
</svg>
"""
