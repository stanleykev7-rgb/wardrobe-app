import os
import uuid
from datetime import datetime, date

from flask import Flask, request, render_template, redirect, url_for, flash, session
from dotenv import load_dotenv

from groq_classifier import classify_garment, classify_garments_multi, ClassificationFailed
from weather import get_current_weather, get_forecast
import recommend
from recommend import suggest_outfit
from outfit_ai import suggest_outfit_ai, OutfitAIFailed
from closet_store import load_closet, save_item, update_item, delete_item
from history_store import log_outfit, get_history, compute_wear_stats, compute_warmth_bias, save_feedback
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


VALID_GENDERS = ("men", "women", "unisex")

# Routes reachable before a profile is selected - everything else redirects
# to pick/create one first. keep-alive is excluded since it's hit by a
# machine (GitHub Actions), not a browsing person.
_PROFILE_EXEMPT_ENDPOINTS = {"profiles_list", "new_profile", "select_profile", "keep_alive", "static"}


@app.before_request
def require_profile():
    if request.endpoint in _PROFILE_EXEMPT_ENDPOINTS:
        return None
    if session.get("profile_id"):
        return None
    try:
        profiles = storage_supabase.list_profiles()
    except Exception:
        profiles = []
    if not profiles:
        return redirect(url_for("new_profile"))
    return redirect(url_for("profiles_list"))


def current_profile_id() -> str:
    return session["profile_id"]


@app.context_processor
def inject_current_profile():
    profile_id = session.get("profile_id")
    if not profile_id:
        return {"current_profile": None}
    try:
        return {"current_profile": storage_supabase.get_profile(profile_id)}
    except Exception:
        return {"current_profile": None}


@app.route("/profiles")
def profiles_list():
    try:
        profiles = storage_supabase.list_profiles()
    except Exception as e:
        flash(f"Could not load profiles: {e}")
        profiles = []
    return render_template("profiles.html", profiles=profiles)


@app.route("/profiles/new", methods=["GET", "POST"])
def new_profile():
    if request.method == "GET":
        try:
            existing = storage_supabase.list_profiles()
        except Exception:
            existing = []
        return render_template("new_profile.html", has_existing_profiles=bool(existing))

    name = request.form.get("name", "").strip()
    gender = request.form.get("gender") if request.form.get("gender") in VALID_GENDERS else "unisex"
    if not name:
        flash("Please enter a name.")
        return redirect(url_for("new_profile"))

    profile_id = uuid.uuid4().hex
    try:
        storage_supabase.create_profile({
            "id": profile_id, "name": name, "gender": gender,
            "created_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        flash(f"Could not create profile: {e}")
        return redirect(url_for("new_profile"))

    session["profile_id"] = profile_id
    flash(f"Welcome, {name}!")
    return redirect(url_for("index"))


@app.route("/profiles/<profile_id>/select", methods=["POST"])
def select_profile(profile_id):
    session["profile_id"] = profile_id
    return redirect(url_for("index"))


@app.route("/profiles/switch")
def switch_profile():
    session.pop("profile_id", None)
    return redirect(url_for("profiles_list"))


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
    closet = load_closet(current_profile_id())

    try:
        wear_stats = compute_wear_stats(get_history(current_profile_id()))
    except Exception:
        wear_stats = {}  # history unavailable shouldn't block viewing the closet
    for item in closet:
        stats = wear_stats.get(item["id"])
        item["wear_count"] = stats["count"] if stats else 0
        item["last_worn"] = stats["last_worn"] if stats else None

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

    is_multi = request.form.get("multi") == "on"

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

    if is_multi:
        return _handle_multi_upload(item_id, processed_path, image_key)
    return _handle_single_upload(item_id, processed_path, image_key)


def _upload_processed_photo(processed_path: str, image_key: str) -> str:
    """Shared by both upload paths: pushes the already-resized local file
    to Supabase Storage, cleans up the local temp copy, and returns the
    public URL. Raises on failure - callers handle the redirect/flash."""
    storage_supabase.upload_photo(processed_path, image_key, "image/jpeg")
    image_url = storage_supabase.public_url_for(image_key)
    if os.path.exists(processed_path):
        os.remove(processed_path)
    return image_url


def _default_attrs() -> dict:
    return {"type": "Unclassified item", "color": "unknown", "zone": "top", "warmth": 5, "waterproof": False, "occasion": "casual"}


def _handle_single_upload(item_id: str, processed_path: str, image_key: str):
    try:
        attrs = classify_garment(processed_path)
        needs_review = False
    except ClassificationFailed:
        attrs = _default_attrs()
        needs_review = True
    except Exception as e:
        flash(f"Unexpected error during classification: {e}")
        attrs = _default_attrs()
        needs_review = True

    try:
        image_url = _upload_processed_photo(processed_path, image_key)
    except Exception as e:
        flash(f"Could not upload photo to storage: {e}")
        if os.path.exists(processed_path):
            os.remove(processed_path)
        return redirect(url_for("index"))

    item = {
        "id": item_id,
        "profile_id": current_profile_id(),
        "image_key": image_key,
        "image_url": image_url,
        "type": attrs.get("type", "unknown"),
        "color": attrs.get("color", "unknown"),
        "warmth": attrs.get("warmth", 5),
        "zone": attrs.get("zone", "top"),
        "waterproof": attrs.get("waterproof", False),
        "occasion": attrs.get("occasion", "casual"),
        "in_laundry": False,
        "needs_review": needs_review,
        "added_at": datetime.utcnow().isoformat(),
    }
    save_item(item)

    if needs_review:
        flash("Auto-detection didn't work this time — this item was saved, please edit its details below.")
    else:
        flash(f"Added {item['color']} {item['type']} (warmth {item['warmth']}/10, zone: {item['zone']}).")
    return redirect(url_for("index"))


def _handle_multi_upload(item_id: str, processed_path: str, image_key: str):
    try:
        detected = classify_garments_multi(processed_path)
        needs_review = False
    except ClassificationFailed:
        # Can't segment the photo without AI, so fall back to ONE
        # needs-review item, same as the single-photo failure path -
        # the photo itself is never lost.
        detected = [_default_attrs()]
        needs_review = True
    except Exception as e:
        flash(f"Unexpected error during classification: {e}")
        detected = [_default_attrs()]
        needs_review = True

    try:
        image_url = _upload_processed_photo(processed_path, image_key)
    except Exception as e:
        flash(f"Could not upload photo to storage: {e}")
        if os.path.exists(processed_path):
            os.remove(processed_path)
        return redirect(url_for("index"))

    added_count = 0
    for i, attrs in enumerate(detected):
        # All detected garments share the same source photo - we can't
        # crop individual items out without real image segmentation, so
        # each entry just points at the same image_key/image_url.
        item = {
            "id": uuid.uuid4().hex if i > 0 else item_id,
            "profile_id": current_profile_id(),
            "image_key": image_key,
            "image_url": image_url,
            "type": attrs.get("type", "unknown"),
            "color": attrs.get("color", "unknown"),
            "warmth": attrs.get("warmth", 5),
            "zone": attrs.get("zone", "top"),
            "waterproof": attrs.get("waterproof", False),
            "occasion": attrs.get("occasion", "casual"),
            "in_laundry": False,
            "needs_review": needs_review,
            "added_at": datetime.utcnow().isoformat(),
        }
        save_item(item)
        added_count += 1

    if needs_review:
        flash("Couldn't automatically split up that photo — saved it as one item, please edit its details below.")
    else:
        flash(f"Added {added_count} item{'s' if added_count != 1 else ''} from that photo — they all share the same picture since it wasn't taken one-per-item.")
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


@app.route("/item/<item_id>/toggle-laundry", methods=["POST"])
def toggle_laundry(item_id):
    currently_in_laundry = request.form.get("current") == "true"
    update_item(item_id, {"in_laundry": not currently_in_laundry})
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

    closet = load_closet(current_profile_id())
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    filtered_closet, occasion_note = filter_by_occasion(closet, occasion)

    try:
        bias = compute_warmth_bias(get_history(current_profile_id()))
    except Exception:
        bias = 0  # history unavailable shouldn't block getting a suggestion

    try:
        outfit = suggest_outfit_ai(filtered_closet, weather, bias=bias)
    except OutfitAIFailed:
        outfit = suggest_outfit(filtered_closet, weather, bias=bias)
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
    target_warmth_raw = request.form.get("target_warmth")
    entry = {
        "log_date": date.today().isoformat(),
        "profile_id": current_profile_id(),
        "occasion": request.form.get("occasion") if request.form.get("occasion") in VALID_OCCASIONS else "casual",
        "top_id": request.form.get("top_id") or None,
        "bottom_id": request.form.get("bottom_id") or None,
        "feet_id": request.form.get("feet_id") or None,
        "head_id": request.form.get("head_id") or None,
        "reasoning": request.form.get("reasoning") or None,
        "temp_c": request.form.get("temp_c") or None,
        "condition": request.form.get("condition") or None,
        "target_warmth": int(target_warmth_raw) if target_warmth_raw else None,
    }
    try:
        log_outfit(entry)
        flash("Logged today's outfit.")
    except Exception as e:
        flash(f"Could not log outfit: {e}")
    return redirect(url_for("suggest", city=request.form.get("city", "")))


@app.route("/history/<log_date>/feedback", methods=["POST"])
def history_feedback(log_date):
    felt = request.form.get("felt")
    if felt not in ("too_cold", "just_right", "too_hot"):
        flash("Invalid feedback value.")
        return redirect(url_for("history"))
    try:
        save_feedback(current_profile_id(), log_date, felt)
        flash("Thanks — future suggestions will take that into account.")
    except Exception as e:
        flash(f"Could not save feedback: {e}")
    return redirect(url_for("history"))


@app.route("/history")
def history():
    entries = get_history(current_profile_id())
    closet_by_id = {item["id"]: item for item in load_closet(current_profile_id())}

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

    closet = load_closet(current_profile_id())
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    filtered_closet, occasion_note = filter_by_occasion(closet, occasion)

    try:
        bias = compute_warmth_bias(get_history(current_profile_id()))
    except Exception:
        bias = 0

    planned_days = recommend.plan_days(filtered_closet, forecast_days, bias=bias)

    days = []
    for planned_day in planned_days:
        days.append({
            "date": planned_day["date"],
            "weather": planned_day["weather"],
            "picks": planned_day["picks"],
            "weather_icon": weather_icon_svg(planned_day["weather"].get("condition", "")),
        })

    return render_template("week.html", days=days, city=city, occasion=occasion, occasion_note=occasion_note)


@app.route("/packing-list")
def packing_list():
    city = request.args.get("city", os.environ.get("DEFAULT_CITY", "Kochi,IN"))
    occasion = request.args.get("occasion", "any")
    try:
        trip_days = max(1, min(5, int(request.args.get("days", 3))))
    except ValueError:
        trip_days = 3

    try:
        forecast_days = get_forecast(city, days=trip_days)
    except Exception as e:
        flash(f"Could not fetch forecast: {e}")
        return redirect(url_for("index"))

    closet = load_closet(current_profile_id())
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    filtered_closet, occasion_note = filter_by_occasion(closet, occasion)

    try:
        bias = compute_warmth_bias(get_history(current_profile_id()))
    except Exception:
        bias = 0

    planned_days = recommend.plan_days(filtered_closet, forecast_days, bias=bias)

    # Aggregate the unique set of items across the whole trip into one
    # packing checklist, grouped by zone, alongside the day-by-day plan.
    seen_ids = set()
    checklist = {"top": [], "bottom": [], "feet": [], "head": []}
    for day in planned_days:
        for zone, item in day["picks"].items():
            if item and item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                checklist[zone].append(item)

    days = [
        {
            "date": d["date"],
            "weather": d["weather"],
            "picks": d["picks"],
            "weather_icon": weather_icon_svg(d["weather"].get("condition", "")),
        }
        for d in planned_days
    ]

    return render_template(
        "packing_list.html", days=days, checklist=checklist, city=city,
        occasion=occasion, occasion_note=occasion_note, trip_days=trip_days,
    )


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
