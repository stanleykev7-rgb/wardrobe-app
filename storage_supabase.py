"""
Supabase storage — a real Postgres table (`items`) for closet metadata,
plus Supabase Storage for garment photos. Chosen over Cloudflare R2
because Supabase's free tier requires no credit card. Trade-off: free
projects auto-pause after 7 days with zero API activity (data is safe,
just offline until resumed) — see /keep-alive in main.py and the GitHub
Actions workflow that pings it periodically to prevent that.

All closet/history functions are scoped to a profile_id, since the app
supports multiple people sharing one deployment, each with their own
closet (see profiles.py / the /profiles routes in main.py).
"""

import os

from supabase import create_client, Client

_client: Client = None


def get_client() -> Client:
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not all([url, key]):
        raise RuntimeError(
            "Supabase credentials missing. Set SUPABASE_URL and SUPABASE_KEY "
            "in your .env file."
        )
    _client = create_client(url, key)
    return _client


def _bucket_name() -> str:
    return os.environ.get("SUPABASE_BUCKET", "wardrobe-photos")


# ---- Photos ----

def upload_photo(local_path: str, object_key: str, content_type: str) -> None:
    client = get_client()
    with open(local_path, "rb") as f:
        client.storage.from_(_bucket_name()).upload(
            object_key, f, {"content-type": content_type}
        )


def delete_photo(object_key: str) -> None:
    client = get_client()
    try:
        client.storage.from_(_bucket_name()).remove([object_key])
    except Exception:
        pass  # best-effort; don't block item deletion on a storage hiccup


def public_url_for(object_key: str) -> str:
    client = get_client()
    return client.storage.from_(_bucket_name()).get_public_url(object_key)


# ---- Profiles ----

def list_profiles() -> list:
    client = get_client()
    result = client.table("profiles").select("*").order("created_at").execute()
    return result.data


def get_profile(profile_id: str):
    client = get_client()
    result = client.table("profiles").select("*").eq("id", profile_id).execute()
    return result.data[0] if result.data else None


def create_profile(profile: dict) -> None:
    client = get_client()
    client.table("profiles").insert(profile).execute()


# ---- Closet metadata (real Postgres table via PostgREST), scoped per profile ----

def load_items(profile_id: str) -> list:
    client = get_client()
    result = (
        client.table("items")
        .select("*")
        .eq("profile_id", profile_id)
        .order("added_at")
        .execute()
    )
    return result.data


def insert_item(item: dict) -> None:
    client = get_client()
    client.table("items").insert(item).execute()


def update_item_row(item_id: str, updates: dict) -> None:
    client = get_client()
    client.table("items").update(updates).eq("id", item_id).execute()


def delete_item_row(item_id: str) -> dict:
    """Deletes the row and returns it (so the caller can also delete its photo)."""
    client = get_client()
    result = client.table("items").select("*").eq("id", item_id).execute()
    row = result.data[0] if result.data else None
    client.table("items").delete().eq("id", item_id).execute()
    return row


def ping() -> None:
    """Lightweight query used by the keep-alive endpoint to count as
    activity and prevent the free-tier project from auto-pausing."""
    client = get_client()
    client.table("items").select("id").limit(1).execute()


# ---- Outfit history (real Postgres table via PostgREST), scoped per profile ----

def save_history_entry(entry: dict) -> None:
    """Upserts on (profile_id, log_date) - logging the same person's same
    day twice updates that day's entry instead of creating a duplicate.
    Different profiles logging the same calendar date are independent."""
    client = get_client()
    client.table("outfit_history").upsert(entry, on_conflict="profile_id,log_date").execute()


def load_history(profile_id: str, limit: int = 60) -> list:
    client = get_client()
    result = (
        client.table("outfit_history")
        .select("*")
        .eq("profile_id", profile_id)
        .order("log_date", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def update_history_feedback(profile_id: str, log_date: str, felt: str) -> None:
    client = get_client()
    (
        client.table("outfit_history")
        .update({"felt": felt})
        .eq("profile_id", profile_id)
        .eq("log_date", log_date)
        .execute()
    )


# ---- Outfit swipes ("Tinder for outfits" feature — see DECISIONS.md ADR-016) ----

def insert_swipe(entry: dict) -> None:
    client = get_client()
    client.table("outfit_swipes").insert(entry).execute()


def load_swipes(profile_id: str, decision: str = None, limit: int = 500) -> list:
    """decision, if given, filters to just 'like'/'dislike'/'save' rows.
    Used by swipe_store.get_disliked_combo_keys() to find combos that have
    been disliked enough times to stop showing in the swipe feed."""
    client = get_client()
    query = client.table("outfit_swipes").select("*").eq("profile_id", profile_id)
    if decision:
        query = query.eq("decision", decision)
    result = query.order("created_at", desc=True).limit(limit).execute()
    return result.data


# ---- Item photo-sharing helper (KNOWN_ISSUES.md #1a fix) ----

def count_items_with_image_key(image_key: str) -> int:
    """How many item rows currently point at this image_key. Called
    AFTER the row being deleted is already gone (see delete_item_row),
    so this naturally counts only the remaining siblings - a shared
    multi-item-photo scan's photo should only be deleted from Storage
    once this reaches zero."""
    client = get_client()
    result = client.table("items").select("id").eq("image_key", image_key).execute()
    return len(result.data)


# ---- Profile deletion cascade (see KNOWN_ISSUES.md #11) ----

def delete_history_for_profile(profile_id: str) -> None:
    client = get_client()
    client.table("outfit_history").delete().eq("profile_id", profile_id).execute()


def delete_swipes_for_profile(profile_id: str) -> None:
    client = get_client()
    client.table("outfit_swipes").delete().eq("profile_id", profile_id).execute()


def delete_profile_row(profile_id: str) -> None:
    client = get_client()
    client.table("profiles").delete().eq("id", profile_id).execute()
