"""
Outfit history — thin wrapper over storage_supabase, mirroring the
closet_store.py pattern so main.py has one consistent way to reach storage.
"""

import storage_supabase


def log_outfit(entry: dict) -> None:
    storage_supabase.save_history_entry(entry)


def get_history(limit: int = 60) -> list:
    return storage_supabase.load_history(limit=limit)


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
