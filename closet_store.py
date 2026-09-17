"""
Closet storage — backed by Supabase (a real Postgres `items` table, see
storage_supabase.py). Scoped per profile_id so each person sharing this
deployment sees only their own closet.
"""

import storage_supabase


def load_closet(profile_id: str) -> list:
    return storage_supabase.load_items(profile_id)


def save_item(item: dict) -> None:
    storage_supabase.insert_item(item)


def update_item(item_id: str, updates: dict) -> None:
    storage_supabase.update_item_row(item_id, updates)


def delete_item(item_id: str) -> None:
    # KNOWN_ISSUES.md #1a: a multi-item photo scan gives several item
    # rows the SAME image_key/image_url (see DECISIONS.md ADR-007 - this
    # is a known, intentional trade-off, not a bug). Deleting one of
    # those siblings must not delete the shared photo out from under the
    # rest - only delete it once no item still references it.
    row = storage_supabase.delete_item_row(item_id)
    if row:
        remaining = storage_supabase.count_items_with_image_key(row["image_key"])
        if remaining == 0:
            storage_supabase.delete_photo(row["image_key"])
