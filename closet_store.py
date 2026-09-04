"""
Closet storage — now backed by R2 (see storage_r2.py) instead of a local
file, so data survives redeploys and restarts on Render's free tier.
Same function signatures as the old local-file version, so main.py
doesn't need to change how it calls this module.
"""

import storage_r2


def load_closet() -> list:
    return storage_r2.load_closet_json()


def save_item(item: dict) -> None:
    closet = storage_r2.load_closet_json()
    closet.append(item)
    storage_r2.save_closet_json(closet)


def update_item(item_id: str, updates: dict) -> None:
    closet = storage_r2.load_closet_json()
    for item in closet:
        if item["id"] == item_id:
            item.update(updates)
            break
    storage_r2.save_closet_json(closet)


def delete_item(item_id: str) -> None:
    closet = storage_r2.load_closet_json()
    item_to_remove = next((i for i in closet if i["id"] == item_id), None)
    closet = [i for i in closet if i["id"] != item_id]
    storage_r2.save_closet_json(closet)
    if item_to_remove:
        storage_r2.delete_photo(item_to_remove["image_key"])
