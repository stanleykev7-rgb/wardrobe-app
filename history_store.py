"""
Outfit history — thin wrapper over storage_supabase, mirroring the
closet_store.py pattern so main.py has one consistent way to reach storage.
"""

import storage_supabase


def log_outfit(entry: dict) -> None:
    storage_supabase.save_history_entry(entry)


def get_history(limit: int = 60) -> list:
    return storage_supabase.load_history(limit=limit)
