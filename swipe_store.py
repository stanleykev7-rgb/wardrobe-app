"""
Storage wrapper for the outfit-swipe feature ("Tinder for outfits") — thin
layer over storage_supabase.py, following the same pattern as
history_store.py and closet_store.py (business logic here, raw
table/query calls stay in storage_supabase.py).

SCOPE NOTE (see DECISIONS.md ADR-016 and ROADMAP.md): swipe feedback is
recorded and used only to stop re-showing a combo the person has already
disliked a couple of times WITHIN THE SWIPE FEED ITSELF. It does NOT yet
feed into the main daily (/suggest) or weekly (/week) suggestion
algorithms — that richer integration (teaching outfit_ai.py's reasoning
about liked/disliked combos generally) is a separate, larger scope
already tracked in ROADMAP.md under "Style learning beyond warmth". This
is a deliberate v1 boundary, not an oversight.
"""
import storage_supabase

# A combo disliked at least this many times stops being generated again
# in the swipe feed. Not configurable via env var (unlike the Groq
# retry/model settings) since it's a product-feel tuning knob, not an
# external-service constant — safe to just edit here if two feels wrong.
DISLIKE_THRESHOLD = 2

ZONES = ("top", "bottom", "feet", "head")


def combo_key(picks: dict) -> tuple:
    """Turns a {zone: item_or_None} picks dict into a hashable
    (top_id, bottom_id, feet_id, head_id) tuple — the same shape used by
    recommend._break_duplicate_combos for duplicate detection, reused
    here for consistency rather than inventing a second convention."""
    return tuple(picks.get(z, {}).get("id") if picks.get(z) else None for z in ZONES)


def record_swipe(profile_id: str, picks: dict, decision: str) -> None:
    """decision is one of 'like', 'dislike', 'save'."""
    top_id, bottom_id, feet_id, head_id = combo_key(picks)
    entry = {
        "profile_id": profile_id,
        "top_id": top_id,
        "bottom_id": bottom_id,
        "feet_id": feet_id,
        "head_id": head_id,
        "decision": decision,
    }
    storage_supabase.insert_swipe(entry)


def get_disliked_combo_keys(profile_id: str) -> set:
    """Returns the set of combo-key tuples disliked >= DISLIKE_THRESHOLD
    times, so the swipe feed's candidate generator can avoid re-showing
    them. Missing zones are represented as None, matching combo_key()."""
    rows = storage_supabase.load_swipes(profile_id, decision="dislike")
    counts = {}
    for row in rows:
        key = (row.get("top_id"), row.get("bottom_id"), row.get("feet_id"), row.get("head_id"))
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= DISLIKE_THRESHOLD}
