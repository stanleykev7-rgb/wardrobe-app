"""
Maps free-text color descriptions (e.g. "navy blue", "charcoal grey") to a
hex color for the outfit mannequin illustration. Groq returns natural-
language color names, not hex codes, so this does simple keyword matching -
checking more specific/compound terms before generic ones.
"""

# Ordered so more specific terms are checked before generic ones
# (e.g. "navy" before "blue", "maroon" before "red").
COLOR_KEYWORDS = [
    ("navy", "#233A5E"),
    ("denim", "#3B5B8C"),
    ("indigo", "#33517E"),
    ("royal blue", "#2255A4"),
    ("sky blue", "#7EB6E0"),
    ("light blue", "#9CC4E4"),
    ("blue", "#3B6EA5"),
    ("maroon", "#6E2B2B"),
    ("burgundy", "#6E2B3A"),
    ("crimson", "#A6333A"),
    ("red", "#B4433A"),
    ("mustard", "#C9962E"),
    ("gold", "#C9A227"),
    ("yellow", "#D9C24B"),
    ("olive", "#6B7A4F"),
    ("khaki", "#B7AD8F"),
    ("beige", "#D9CFB8"),
    ("tan", "#C9A876"),
    ("cream", "#EDE6D3"),
    ("ivory", "#F0ECDD"),
    ("charcoal", "#3A3D40"),
    ("grey", "#8A8D88"),
    ("gray", "#8A8D88"),
    ("black", "#232629"),
    ("white", "#F4F3EE"),
    ("brown", "#7A5A3C"),
    ("camel", "#B78D5D"),
    ("orange", "#C97A3D"),
    ("forest green", "#3F5D45"),
    ("olive green", "#6B7A4F"),
    ("green", "#5C7A5E"),
    ("teal", "#3E7A79"),
    ("purple", "#6B4E86"),
    ("lavender", "#B9A9D4"),
    ("pink", "#D896A6"),
    ("magenta", "#B84C86"),
    ("silver", "#B6B8B3"),
]

DEFAULT_COLOR = "#8A8D88"  # neutral grey fallback for unrecognized colors


def color_to_hex(color_text: str) -> str:
    if not color_text:
        return DEFAULT_COLOR
    text = color_text.lower().strip()
    for keyword, hex_val in COLOR_KEYWORDS:
        if keyword in text:
            return hex_val
    return DEFAULT_COLOR
