import os
import json
import uuid
from datetime import datetime

from flask import Flask, request, render_template, redirect, url_for, flash
from dotenv import load_dotenv

from groq_classifier import classify_garment, ClassificationFailed
from weather import get_current_weather
from recommend import suggest_outfit
from closet_store import load_closet, save_item, update_item, delete_item
from image_utils import process_image
import storage_supabase

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# Temp scratch space only - the real, persistent copy lives in R2.
TMP_DIR = "/tmp/wardrobe-uploads"
os.makedirs(TMP_DIR, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    closet = load_closet()
    return render_template("index.html", closet=closet)


@app.route("/upload", methods=["POST"])
def upload():
    if "photo" not in request.files:
        flash("No file part in the request.")
        return redirect(url_for("index"))

    file = request.files["photo"]
    if file.filename == "":
        flash("No file selected.")
        return redirect(url_for("index"))

    if not allowed_file(file.filename):
        flash("Unsupported file type. Use png, jpg, jpeg, or webp.")
        return redirect(url_for("index"))

    item_id = uuid.uuid4().hex
    raw_path = os.path.join(TMP_DIR, f"{item_id}_raw")
    processed_path = os.path.join(TMP_DIR, f"{item_id}.jpg")
    file.save(raw_path)

    # Resize/compress before it ever touches storage or the Groq API.
    try:
        process_image(raw_path, processed_path)
    except Exception as e:
        flash(f"Could not process image: {e}")
        return redirect(url_for("index"))
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)

    image_key = f"photos/{item_id}.jpg"

    # Classify first, while the processed file still exists locally.
    try:
        attrs = classify_garment(processed_path)
        needs_review = False
    except ClassificationFailed:
        attrs = {"type": "Unclassified item", "color": "unknown", "zone": "top", "warmth": 5, "waterproof": False}
        needs_review = True
    except Exception as e:
        flash(f"Unexpected error during classification: {e}")
        attrs = {"type": "Unclassified item", "color": "unknown", "zone": "top", "warmth": 5, "waterproof": False}
        needs_review = True

    # Then upload the same local file to Supabase Storage and clean up.
    try:
        storage_supabase.upload_photo(processed_path, image_key, "image/jpeg")
        image_url = storage_supabase.public_url_for(image_key)
    except Exception as e:
        flash(f"Could not upload photo to storage: {e}")
        return redirect(url_for("index"))
    finally:
        if os.path.exists(processed_path):
            os.remove(processed_path)

    item = {
        "id": item_id,
        "image_key": image_key,
        "image_url": image_url,
        "type": attrs.get("type", "unknown"),
        "color": attrs.get("color", "unknown"),
        "warmth": attrs.get("warmth", 5),
        "zone": attrs.get("zone", "top"),
        "waterproof": attrs.get("waterproof", False),
        "needs_review": needs_review,
        "added_at": datetime.utcnow().isoformat(),
    }
    save_item(item)

    if needs_review:
        flash("Auto-detection didn't work this time — this item was saved, please edit its details below.")
    else:
        flash(f"Added {item['color']} {item['type']} (warmth {item['warmth']}/10, zone: {item['zone']}).")
    return redirect(url_for("index"))


@app.route("/item/<item_id>/edit", methods=["POST"])
def edit_item(item_id):
    updates = {
        "type": request.form.get("type", "").strip() or "unknown item",
        "color": request.form.get("color", "").strip() or "unknown",
        "zone": request.form.get("zone") if request.form.get("zone") in ("head", "top", "bottom", "feet") else "top",
        "warmth": max(1, min(10, int(request.form.get("warmth", 5) or 5))),
        "waterproof": request.form.get("waterproof") == "on",
        "needs_review": False,
    }
    update_item(item_id, updates)
    flash("Item updated.")
    return redirect(url_for("index"))


@app.route("/item/<item_id>/delete", methods=["POST"])
def delete_item_route(item_id):
    delete_item(item_id)
    flash("Item removed.")
    return redirect(url_for("index"))


@app.route("/suggest")
def suggest():
    city = request.args.get("city", os.environ.get("DEFAULT_CITY", "Kochi,IN"))

    try:
        weather = get_current_weather(city)
    except Exception as e:
        flash(f"Could not fetch weather: {e}")
        return redirect(url_for("index"))

    closet = load_closet()
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    outfit = suggest_outfit(closet, weather)

    return render_template("suggest.html", weather=weather, outfit=outfit, city=city)


@app.route("/keep-alive")
def keep_alive():
    """
    Hit by a scheduled GitHub Actions ping (see .github/workflows/keep-alive.yml)
    so Supabase sees regular API activity and doesn't auto-pause the free
    project after 7 days of inactivity. Also incidentally wakes this app
    if it's a Render/Railway free instance that spun down from idling.
    """
    try:
        storage_supabase.ping()
        return {"status": "ok"}, 200
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(debug=debug_mode, port=int(os.environ.get("PORT", 5000)))
