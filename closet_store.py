"""
Very simple JSON-file based storage for the closet.
Good enough to get started; swap for a real database (SQLite/Postgres) later.
"""

import json
import os
import threading

DATA_FILE = os.path.join(os.path.dirname(__file__), "closet.json")
_lock = threading.Lock()


def load_closet() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    with _lock:
        with open(DATA_FILE, "r") as f:
            return json.load(f)


def save_item(item: dict) -> None:
    with _lock:
        closet = []
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                closet = json.load(f)
        closet.append(item)
        with open(DATA_FILE, "w") as f:
            json.dump(closet, f, indent=2)


def delete_item(item_id: str) -> None:
    with _lock:
        if not os.path.exists(DATA_FILE):
            return
        with open(DATA_FILE, "r") as f:
            closet = json.load(f)
        closet = [i for i in closet if i["id"] != item_id]
        with open(DATA_FILE, "w") as f:
            json.dump(closet, f, indent=2)
