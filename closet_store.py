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
    row = storage_supabase.delete_item_row(item_id)
    if row:
        storage_supabase.delete_photo(row["image_key"])
