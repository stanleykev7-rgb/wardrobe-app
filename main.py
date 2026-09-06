import os
import uuid
from datetime import datetime, date

from flask import Flask, request, render_template, redirect, url_for, flash
from dotenv import load_dotenv

from groq_classifier import classify_garment, ClassificationFailed
from weather import get_current_weather, get_forecast
import recommend
from recommend import suggest_outfit
from outfit_ai import suggest_outfit_ai, OutfitAIFailed
from closet_store import load_closet, save_item, update_item, delete_item
from history_store import log_outfit, get_history
from image_utils import process_image
from weather_icons import weather_icon_svg
from mannequin import mannequin_svg
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


VALID_OCCASIONS = ("casual", "work", "formal", "gym")


def filter_by_occasion(closet: list, occasion: str) -> tuple:
    """Returns (filtered_closet, note_or_None). Falls back to the full
    closet with an explanatory note if filtering would leave nothing to
    suggest from, rather than dead-ending on an empty result."""
    if not occasion or occasion == "any":
        return closet, None
    filtered = [i for i in closet if i.get("occasion", "casual") == occasion]
    if not filtered:
        return closet, f"No {occasion} items found in your closet — showing suggestions from your full closet instead."
    return filtered, None


CATEGORY_ORDER = [
    ("needs_review", "Needs review"),
    ("top", "Tops"),
    ("bottom", "Bottoms"),
    ("feet", "Feet"),
    ("head", "Head"),
]


def group_closet_by_category(closet: list) -> list:
    """Groups items for the collapsible-sections UI: anything flagged
    needs_review goes in its own bucket regardless of zone (so it's easy
    to find and fix), everything else is grouped by zone."""
    buckets = {key: [] for key, _ in CATEGORY_ORDER}
    for item in closet:
        if item.get("needs_review"):
            buckets["needs_review"].append(item)
        else:
            zone = item.get("zone", "top")
            buckets.setdefault(zone, []).append(item)

    return [
        {"key": key, "label": label, "garments": buckets[key]}
        for key, label in CATEGORY_ORDER
        if buckets[key]
    ]


@app.route("/")
def index():
    closet = load_closet()
    categories = group_closet_by_category(closet)
    return render_template("index.html", categories=categories, closet_count=len(closet))


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
        attrs = {"type": "Unclassified item", "color": "unknown", "zone": "top", "warmth": 5, "waterproof": False, "occasion": "casual"}
        needs_review = True
    except Exception as e:
        flash(f"Unexpected error during classification: {e}")
        attrs = {"type": "Unclassified item", "color": "unknown", "zone": "top", "warmth": 5, "waterproof": False, "occasion": "casual"}
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
        "occasion": attrs.get("occasion", "casual"),
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
        "occasion": request.form.get("occasion") if request.form.get("occasion") in VALID_OCCASIONS else "casual",
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
    occasion = request.args.get("occasion", "any")

    try:
        weather = get_current_weather(city)
    except Exception as e:
        flash(f"Could not fetch weather: {e}")
        return redirect(url_for("index"))

    closet = load_closet()
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    filtered_closet, occasion_note = filter_by_occasion(closet, occasion)

    try:
        outfit = suggest_outfit_ai(filtered_closet, weather)
    except OutfitAIFailed:
        outfit = suggest_outfit(filtered_closet, weather)
        outfit["notes"] = outfit.get("notes", []) + ["Styling suggestion unavailable right now — showing closest-warmth picks instead."]

    if occasion_note:
        outfit["notes"] = [occasion_note] + outfit.get("notes", [])

    return render_template(
        "suggest.html", weather=weather, outfit=outfit, city=city, occasion=occasion,
        weather_icon=weather_icon_svg(weather.get("condition", "")),
        mannequin=mannequin_svg(outfit["picks"]),
    )


@app.route("/log-outfit", methods=["POST"])
def log_outfit_route():
    entry = {
        "log_date": date.today().isoformat(),
        "occasion": request.form.get("occasion") if request.form.get("occasion") in VALID_OCCASIONS else "casual",
        "top_id": request.form.get("top_id") or None,
        "bottom_id": request.form.get("bottom_id") or None,
        "feet_id": request.form.get("feet_id") or None,
        "head_id": request.form.get("head_id") or None,
        "reasoning": request.form.get("reasoning") or None,
        "temp_c": request.form.get("temp_c") or None,
        "condition": request.form.get("condition") or None,
    }
    try:
        log_outfit(entry)
        flash("Logged today's outfit.")
    except Exception as e:
        flash(f"Could not log outfit: {e}")
    return redirect(url_for("suggest", city=request.form.get("city", "")))


@app.route("/history")
def history():
    entries = get_history()
    closet_by_id = {item["id"]: item for item in load_closet()}

    for entry in entries:
        for zone in ("top", "bottom", "feet", "head"):
            item_id = entry.get(f"{zone}_id")
            entry[f"{zone}_item"] = closet_by_id.get(item_id) if item_id else None

    return render_template("history.html", entries=entries)


@app.route("/week")
def week():
    city = request.args.get("city", os.environ.get("DEFAULT_CITY", "Kochi,IN"))
    occasion = request.args.get("occasion", "any")

    try:
        forecast_days = get_forecast(city, days=5)
    except Exception as e:
        flash(f"Could not fetch forecast: {e}")
        return redirect(url_for("index"))

    closet = load_closet()
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    filtered_closet, occasion_note = filter_by_occasion(closet, occasion)

    days = []
    previous_picks = {"top": None, "bottom": None, "feet": None, "head": None}
    for day_weather in forecast_days:
        temp = day_weather.get("feels_like_c", day_weather.get("temp_c", 20))
        target = recommend.target_warmth(temp)
        need_waterproof = day_weather.get("rain", False)

        picks = {}
        for zone in recommend.ZONES_REQUIRED + recommend.ZONES_OPTIONAL:
            avoid_id = previous_picks[zone]["id"] if previous_picks[zone] else None
            picks[zone] = recommend.pick_for_zone_with_variety(filtered_closet, zone, target, need_waterproof, avoid_id=avoid_id)
        previous_picks = picks

        days.append({
            "date": day_weather["date"],
            "weather": day_weather,
            "picks": picks,
            "weather_icon": weather_icon_svg(day_weather.get("condition", "")),
        })

    return render_template("week.html", days=days, city=city, occasion=occasion, occasion_note=occasion_note)


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
