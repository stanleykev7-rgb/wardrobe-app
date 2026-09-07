"""
Outfit history — thin wrapper over storage_supabase, scoped per profile_id
so each person's logged outfits, wear stats, and feedback bias stay
separate from anyone else sharing this deployment.
"""

import storage_supabase


def log_outfit(entry: dict) -> None:
    storage_supabase.save_history_entry(entry)


def get_history(profile_id: str, limit: int = 60) -> list:
    return storage_supabase.load_history(profile_id, limit=limit)


def compute_wear_stats(history: list) -> dict:
    """
    Returns {item_id: {"count": int, "last_worn": "YYYY-MM-DD"}} derived
    from logged history entries. Items never logged simply won't appear
    in the result - callers should treat a missing id as a 0-wear item.
    """
    stats = {}
    for entry in history:
        for zone in ("top", "bottom", "feet", "head"):
            item_id = entry.get(f"{zone}_id")
            if not item_id:
                continue
            if item_id not in stats:
                stats[item_id] = {"count": 0, "last_worn": entry["log_date"]}
            stats[item_id]["count"] += 1
            if entry["log_date"] > stats[item_id]["last_worn"]:
                stats[item_id]["last_worn"] = entry["log_date"]
    return stats


def compute_warmth_bias(history: list, limit: int = 20) -> int:
    """
    Looks at the most recent entries that have BOTH feedback ("felt") and
    a recorded target_warmth, and derives a persistent adjustment: if
    outfits have tended to run cold, a positive bias nudges future
    suggestions warmer; if they've run hot, negative. Clamped to [-2, 2]
    so one bad week can't wildly overcorrect future suggestions.
    """
    scored = [h for h in history if h.get("felt") and h.get("target_warmth") is not None][:limit]
    if not scored:
        return 0

    adjustments = []
    for entry in scored:
        if entry["felt"] == "too_cold":
            adjustments.append(1)
        elif entry["felt"] == "too_hot":
            adjustments.append(-1)
        else:
            adjustments.append(0)

    avg = sum(adjustments) / len(adjustments)
    return max(-2, min(2, round(avg)))


def save_feedback(profile_id: str, log_date: str, felt: str) -> None:
    storage_supabase.update_history_feedback(profile_id, log_date, felt)
