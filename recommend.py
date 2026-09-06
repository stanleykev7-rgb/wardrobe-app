"""
Rule-based outfit picker (same spirit as ToWear's warmth-scoring idea).
This is also the FALLBACK used if the AI-based outfit combo suggestion
(outfit_ai.py) fails for any reason - so the app always returns something.
"""

ZONES_REQUIRED = ["top", "bottom", "feet"]
ZONES_OPTIONAL = ["head"]


def target_warmth(temp_c: float, bias: int = 0) -> int:
    """Rough mapping of temperature to a 1-10 target warmth score.
    `bias` (typically from history_store.compute_warmth_bias) nudges the
    result up or down based on feedback about past suggestions - e.g. if
    the person has said outfits ran cold several times, a positive bias
    pushes future suggestions warmer."""
    if temp_c >= 30:
        base = 1
    elif temp_c >= 25:
        base = 2
    elif temp_c >= 20:
        base = 4
    elif temp_c >= 15:
        base = 5
    elif temp_c >= 10:
        base = 7
    elif temp_c >= 5:
        base = 8
    elif temp_c >= 0:
        base = 9
    else:
        base = 10
    return max(1, min(10, base + bias))


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


def plan_days(closet: list, forecast_days: list, bias: int = 0) -> list:
    """
    Given a list of daily weather summaries (from weather.get_forecast),
    plans one outfit per day. Unlike a simple "avoid yesterday" rule, this
    tracks how many times each item has been used ACROSS THE WHOLE WEEK
    so far and always prefers whichever reasonable candidate has been used
    least - so a 5-day plan rotates through everything appropriate in your
    closet before it repeats anything, not just avoiding immediate repeats.

    A final pass also checks for any two days that ended up with the
    IDENTICAL full outfit (all four zones matching) and swaps one zone to
    break the duplicate, if an alternative exists.

    Returns: [{"date": str, "weather": dict, "picks": {zone: item_or_None}}, ...]
    """
    days = []
    usage_count = {"top": {}, "bottom": {}, "feet": {}, "head": {}}

    for day_weather in forecast_days:
        temp = day_weather.get("feels_like_c", day_weather.get("temp_c", 20))
        target = target_warmth(temp, bias)
        need_waterproof = day_weather.get("rain", False)

        picks = {}
        for zone in ZONES_REQUIRED + ZONES_OPTIONAL:
            candidates = candidates_for_zone(closet, zone, target, need_waterproof, limit=8)
            if not candidates:
                picks[zone] = None
                continue
            # candidates is already sorted by closeness-to-target; among
            # ties on usage count, min() keeps that ordering, so we still
            # favor better-fitting items among equally-unused ones.
            best = min(candidates, key=lambda c: usage_count[zone].get(c["id"], 0))
            picks[zone] = best
            usage_count[zone][best["id"]] = usage_count[zone].get(best["id"], 0) + 1

        days.append({"date": day_weather["date"], "weather": day_weather, "picks": picks})

    _break_duplicate_combos(days, closet, bias)
    return days


def _break_duplicate_combos(days: list, closet: list, bias: int) -> None:
    """Mutates `days` in place: if two days ended up with the exact same
    four-zone combo, swaps one zone on the later day to a fresh candidate
    if one exists. A best-effort pass, not a hard guarantee - a very small
    closet may simply not have enough variety to avoid all repeats."""
    seen_combos = set()
    for day in days:
        combo = tuple(
            day["picks"][z]["id"] if day["picks"][z] else None
            for z in ("top", "bottom", "feet", "head")
        )
        if combo in seen_combos:
            temp = day["weather"].get("feels_like_c", day["weather"].get("temp_c", 20))
            target = target_warmth(temp, bias)
            need_waterproof = day["weather"].get("rain", False)
            for zone in ZONES_REQUIRED + ZONES_OPTIONAL:
                current = day["picks"][zone]
                candidates = candidates_for_zone(closet, zone, target, need_waterproof, limit=8)
                alt = next((c for c in candidates if not current or c["id"] != current["id"]), None)
                if alt:
                    day["picks"][zone] = alt
                    break
        seen_combos.add(combo)


def suggest_outfit(closet: list, weather: dict, bias: int = 0) -> dict:
    """
    closet: list of item dicts (see closet_store)
    weather: dict from weather.get_current_weather
    bias: warmth adjustment from history_store.compute_warmth_bias, based
    on past feedback about whether suggestions ran warm or cold.
    Returns: {"picks": {zone: item_or_None}, "target_warmth": int, "notes": [str], "reasoning": None}
    `reasoning` is always None here since this is the non-AI fallback - main.py
    only fills it in when outfit_ai.py's coordinated suggestion succeeds.
    """
    temp = weather.get("feels_like_c", weather.get("temp_c", 20))
    target = target_warmth(temp, bias)
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
