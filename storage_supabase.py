"""
Supabase storage — a real Postgres table (`items`) for closet metadata,
plus Supabase Storage for garment photos. Chosen over Cloudflare R2
because Supabase's free tier requires no credit card. Trade-off: free
projects auto-pause after 7 days with zero API activity (data is safe,
just offline until resumed) — see /keep-alive in main.py and the GitHub
Actions workflow that pings it periodically to prevent that.
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


# ---- Closet metadata (real Postgres table via PostgREST) ----

def load_items() -> list:
    client = get_client()
    result = client.table("items").select("*").order("added_at").execute()
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


# ---- Outfit history (real Postgres table via PostgREST) ----

def save_history_entry(entry: dict) -> None:
    """Upserts on log_date - logging the same day twice updates that
    day's entry instead of creating a duplicate."""
    client = get_client()
    client.table("outfit_history").upsert(entry, on_conflict="log_date").execute()


def load_history(limit: int = 60) -> list:
    client = get_client()
    result = (
        client.table("outfit_history")
        .select("*")
        .order("log_date", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
