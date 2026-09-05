"""
Rule-based outfit picker (same spirit as ToWear's warmth-scoring idea).
This is also the FALLBACK used if the AI-based outfit combo suggestion
(outfit_ai.py) fails for any reason - so the app always returns something.
"""

ZONES_REQUIRED = ["top", "bottom", "feet"]
ZONES_OPTIONAL = ["head"]


def target_warmth(temp_c: float) -> int:
    """Rough mapping of temperature to a 1-10 target warmth score."""
    if temp_c >= 30:
        return 1
    if temp_c >= 25:
        return 2
    if temp_c >= 20:
        return 4
    if temp_c >= 15:
        return 5
    if temp_c >= 10:
        return 7
    if temp_c >= 5:
        return 8
    if temp_c >= 0:
        return 9
    return 10


def candidates_for_zone(closet: list, zone: str, target: int, need_waterproof: bool, limit: int = 4) -> list:
    """Returns up to `limit` items for a zone, sorted by closeness to the
    target warmth score. Used both by the plain rule-based picker (limit=1)
    and by outfit_ai.py, which wants a short-list of options to reason over
    rather than a single forced pick."""
    candidates = [i for i in closet if i.get("zone") == zone]
    if not candidates:
        return []

    if need_waterproof:
        waterproof_candidates = [i for i in candidates if i.get("waterproof")]
        if waterproof_candidates:
            candidates = waterproof_candidates

    candidates = sorted(candidates, key=lambda i: abs(i.get("warmth", 5) - target))
    return candidates[:limit]


def suggest_outfit(closet: list, weather: dict) -> dict:
    """
    closet: list of item dicts (see closet_store)
    weather: dict from weather.get_current_weather
    Returns: {"picks": {zone: item_or_None}, "target_warmth": int, "notes": [str], "reasoning": None}
    `reasoning` is always None here since this is the non-AI fallback - main.py
    only fills it in when outfit_ai.py's coordinated suggestion succeeds.
    """
    temp = weather.get("feels_like_c", weather.get("temp_c", 20))
    target = target_warmth(temp)
    need_waterproof = weather.get("rain", False)

    picks = {}
    notes = []

    for zone in ZONES_REQUIRED + ZONES_OPTIONAL:
        top_candidates = candidates_for_zone(closet, zone, target, need_waterproof, limit=1)
        item = top_candidates[0] if top_candidates else None
        picks[zone] = item
        if item is None and zone in ZONES_REQUIRED:
            notes.append(f"No {zone} items in your closet yet — add some for full suggestions.")

    if need_waterproof and not any(
        picks[z] and picks[z].get("waterproof") for z in ZONES_REQUIRED if picks[z]
    ):
        notes.append("Rain is expected but no waterproof items were found — consider bringing an umbrella.")

    return {
        "picks": picks,
        "target_warmth": target,
        "notes": notes,
        "reasoning": None,
    }
