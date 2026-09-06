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
    rather than a single forced pick. Items marked in_laundry are excluded
    entirely - they're not available to wear right now."""
    candidates = [i for i in closet if i.get("zone") == zone and not i.get("in_laundry")]
    if not candidates:
        return []

    if need_waterproof:
        waterproof_candidates = [i for i in candidates if i.get("waterproof")]
        if waterproof_candidates:
            candidates = waterproof_candidates

    candidates = sorted(candidates, key=lambda i: abs(i.get("warmth", 5) - target))
    return candidates[:limit]


def pick_for_zone_with_variety(closet: list, zone: str, target: int, need_waterproof: bool, avoid_id: str = None):
    """Same idea as candidates_for_zone, but for the weekly view: prefers
    an item other than `avoid_id` (typically the previous day's pick for
    this zone) when an equally-reasonable alternative exists, so a 5-day
    plan doesn't suggest the identical top three days running."""
    candidates = candidates_for_zone(closet, zone, target, need_waterproof, limit=3)
    if not candidates:
        return None
    if avoid_id:
        alternatives = [c for c in candidates if c["id"] != avoid_id]
        if alternatives:
            return alternatives[0]
    return candidates[0]


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
        "style_score": None,
        "verdict": None,
    }
