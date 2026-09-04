"""
Simple rule-based outfit picker (same spirit as ToWear's warmth-scoring idea,
just without the linear-regression personalization layer).

Maps a temperature to a "target warmth" range, then picks the closest-scoring
item available in each body zone (top, bottom, feet, head-optional).
"""

ZONES_REQUIRED = ["top", "bottom", "feet"]
ZONES_OPTIONAL = ["head"]


def _target_warmth(temp_c: float) -> int:
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


def _best_item_for_zone(closet: list, zone: str, target: int, need_waterproof: bool):
    candidates = [i for i in closet if i.get("zone") == zone]
    if not candidates:
        return None

    if need_waterproof:
        waterproof_candidates = [i for i in candidates if i.get("waterproof")]
        if waterproof_candidates:
            candidates = waterproof_candidates

    # Pick the item whose warmth score is closest to the target
    return min(candidates, key=lambda i: abs(i.get("warmth", 5) - target))


def suggest_outfit(closet: list, weather: dict) -> dict:
    """
    closet: list of item dicts (see closet_store)
    weather: dict from weather.get_current_weather
    Returns: {"picks": {zone: item_or_None}, "target_warmth": int, "notes": [str]}
    """
    temp = weather.get("feels_like_c", weather.get("temp_c", 20))
    target = _target_warmth(temp)
    need_waterproof = weather.get("rain", False)

    picks = {}
    notes = []

    for zone in ZONES_REQUIRED + ZONES_OPTIONAL:
        item = _best_item_for_zone(closet, zone, target, need_waterproof)
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
    }
