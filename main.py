import os
import sys
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
from avatar_ai import generate_avatar_image, AvatarGenerationFailed
import swipe_store
import storage_supabase
import base64

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

if app.secret_key == "dev-secret-change-me":
    # See KNOWN_ISSUES.md #1's compounding finding: the session cookie
    # storing profile_id is the only thing gating access to a profile's
    # data. If it's signed with this hardcoded, publicly-visible string,
    # anyone can forge a valid session cookie for any profile_id without
    # ever going through /profiles/select. Printed to stderr (not raised)
    # so an existing deployment that hasn't set this yet doesn't suddenly
    # start refusing to boot - but it should be set for real use.
    print(
        "WARNING: FLASK_SECRET_KEY is not set - using an insecure default. "
        "Session cookies (which gate access to profile data) can be forged. "
        "Set FLASK_SECRET_KEY to a real random value in your deployment environment.",
        file=sys.stderr,
    )

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
_PROFILE_EXEMPT_ENDPOINTS = {"profiles_list", "new_profile", "select_profile", "delete_profile_route", "keep_alive", "static"}


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


@app.route("/profiles/<profile_id>/delete", methods=["POST"])
def delete_profile_route(profile_id):
    """
    Deletes a profile and cascades: every item it owns (via the existing
    shared-photo-safe delete_item — see KNOWN_ISSUES.md #1a), every
    outfit_history row, and every outfit_swipes row. This is an explicit,
    user-confirmed destructive action (native confirm() dialog on the
    delete button, per this app's established convention — see
    UI_GUIDELINES.md) — deleting a profile intentionally deletes
    everything that was only ever reachable through it, rather than
    orphaning that data (see KNOWN_ISSUES.md #11).
    """
    for item in load_closet(profile_id):
        delete_item(item["id"])

    try:
        storage_supabase.delete_history_for_profile(profile_id)
    except Exception as e:
        flash(f"Could not delete outfit history: {e}")

    try:
        storage_supabase.delete_swipes_for_profile(profile_id)
    except Exception as e:
        flash(f"Could not delete swipe data: {e}")

    try:
        storage_supabase.delete_profile_row(profile_id)
    except Exception as e:
        flash(f"Could not delete profile: {e}")
        return redirect(url_for("profiles_list"))

    if session.get("profile_id") == profile_id:
        session.pop("profile_id", None)

    flash("Profile deleted.")
    return redirect(url_for("profiles_list"))


@app.route("/profiles/switch")
def switch_profile():
    session.pop("profile_id", None)
    return redirect(url_for("profiles_list"))


VALID_OCCASIONS = ("casual", "work", "semi_formal", "formal", "party", "gym")


def get_item_occasions(item: dict) -> list:
    """Reads an item's occasions list, falling back gracefully for items
    saved before multi-occasion tagging existed (which only had a single
    'occasion' string, or nothing at all)."""
    occasions = item.get("occasions")
    if isinstance(occasions, list) and occasions:
        return occasions
    legacy = item.get("occasion")
    return [legacy] if legacy else ["casual"]


app.jinja_env.globals["get_item_occasions"] = get_item_occasions
app.jinja_env.globals["ALL_OCCASIONS"] = ("casual", "work", "semi_formal", "formal", "party", "gym")


def filter_by_occasion(closet: list, occasion: str) -> tuple:
    """Returns (filtered_closet, note_or_None). Falls back to the full
    closet with an explanatory note if filtering would leave nothing to
    suggest from, rather than dead-ending on an empty result."""
    if not occasion or occasion == "any":
        return closet, None
    filtered = [i for i in closet if occasion in get_item_occasions(i)]
    if not filtered:
        return closet, f"No {occasion} items found in your closet — showing suggestions from your full closet instead."
    return filtered, None


CATEGORY_ORDER = [
    ("needs_review", "Needs review"),
    ("dress", "Dresses"),
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
    # request.files.getlist("photo") returns every file submitted under
    # that field name - normally just one, but the upload form now also
    # supports selecting several photos at once (batch upload) and/or a
    # separate camera-capture input, both sharing the same field name.
    files = [f for f in request.files.getlist("photo") if f and f.filename]
    if not files:
        flash("No file selected.")
        return redirect(url_for("index"))

    is_multi = request.form.get("multi") == "on"
    all_saved_items = []
    skipped = []  # [(filename, reason), ...] for photos that couldn't be processed at all

    for file in files:
        if not allowed_file(file.filename):
            skipped.append((file.filename, "unsupported file type"))
            continue

        item_id = uuid.uuid4().hex
        raw_path = os.path.join(TMP_DIR, f"{item_id}_raw")
        processed_path = os.path.join(TMP_DIR, f"{item_id}.jpg")
        file.save(raw_path)

        # Resize/compress before it ever touches storage or the Groq API.
        try:
            process_image(raw_path, processed_path)
        except Exception as e:
            skipped.append((file.filename, f"could not process image ({e})"))
            if os.path.exists(raw_path):
                os.remove(raw_path)
            continue
        finally:
            if os.path.exists(raw_path):
                os.remove(raw_path)

        image_key = f"photos/{item_id}.jpg"
        if is_multi:
            saved_items, error = _process_multi_upload(item_id, processed_path, image_key)
        else:
            saved_items, error = _process_single_upload(item_id, processed_path, image_key)

        if error:
            skipped.append((file.filename, error))
        else:
            all_saved_items.extend(saved_items)

    _flash_upload_summary(len(files), all_saved_items, skipped, is_multi)
    return redirect(url_for("index"))


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
    return {"type": "Unclassified item", "color": "unknown", "description": "", "zone": "top", "warmth": 5, "waterproof": False, "occasions": ["casual"]}


# Alphabet deliberately excludes visually ambiguous characters (0/O, 1/I/L)
# since this code is meant to be read off a garment tag by a person, not
# just stored - readability matters more than a slightly larger keyspace.
_ITEM_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_item_code() -> str:
    """A short, human-readable identifier distinct from the internal
    database id (a 32-char UUID, never shown to the user) - think of it
    like a real clothing tag's style/SKU number. Its job is to give each
    item a stable, referenceable "name" a person (or a future VTON
    pipeline) can point to unambiguously; distinguishing visually similar
    items (e.g. two striped dresses) is the description field's job, not
    this code's. Format: XXX-XXX, e.g. 7K3-F9X.

    Collision risk is deliberately accepted rather than checked/retried:
    32 symbols^6 ≈ 1 billion combinations is comfortably safe for a
    personal wardrobe's realistic size, and this is a display
    convenience, not a security or correctness-critical identifier (the
    real primary key is still the UUID id) - not worth the complexity of
    a uniqueness check for this use case."""
    import secrets
    chars = [secrets.choice(_ITEM_CODE_ALPHABET) for _ in range(6)]
    return "".join(chars[:3]) + "-" + "".join(chars[3:])


def _process_single_upload(item_id: str, processed_path: str, image_key: str):
    """Classifies and saves ONE garment from ONE already-processed photo.
    Returns (saved_items, error_or_None) - saved_items is a list of 0 or
    1 item dicts (kept as a list so /upload can treat this and
    _process_multi_upload's result uniformly when aggregating a batch)."""
    try:
        attrs = classify_garment(processed_path)
        needs_review = False
    except ClassificationFailed as e:
        print(f"WARNING: classification failed for item {item_id}: {e}", file=sys.stderr)
        attrs = _default_attrs()
        needs_review = True
    except Exception as e:
        print(f"WARNING: unexpected error classifying item {item_id}: {type(e).__name__}: {e}", file=sys.stderr)
        attrs = _default_attrs()
        needs_review = True

    try:
        image_url = _upload_processed_photo(processed_path, image_key)
    except Exception as e:
        if os.path.exists(processed_path):
            os.remove(processed_path)
        return [], f"could not upload photo to storage ({e})"

    item = {
        "id": item_id,
        "item_code": generate_item_code(),
        "profile_id": current_profile_id(),
        "image_key": image_key,
        "image_url": image_url,
        "type": attrs.get("type", "unknown"),
        "color": attrs.get("color", "unknown"),
        "description": attrs.get("description", ""),
        "warmth": attrs.get("warmth", 5),
        "zone": attrs.get("zone", "top"),
        "waterproof": attrs.get("waterproof", False),
        "occasions": attrs.get("occasions", ["casual"]),
        "in_laundry": False,
        "needs_review": needs_review,
        "added_at": datetime.utcnow().isoformat(),
    }
    save_item(item)
    return [item], None


def _process_multi_upload(item_id: str, processed_path: str, image_key: str):
    """Same idea as _process_single_upload, but for a photo expected to
    contain SEVERAL garments (classify_garments_multi) - see
    DECISIONS.md ADR-007 for why all detected items share one photo.
    Returns (saved_items, error_or_None)."""
    try:
        detected = classify_garments_multi(processed_path)
        needs_review = False
    except ClassificationFailed as e:
        # Can't segment the photo without AI, so fall back to ONE
        # needs-review item, same as the single-photo failure path -
        # the photo itself is never lost.
        print(f"WARNING: multi-item classification failed for item {item_id}: {e}", file=sys.stderr)
        detected = [_default_attrs()]
        needs_review = True
    except Exception as e:
        print(f"WARNING: unexpected error in multi-item classification for {item_id}: {type(e).__name__}: {e}", file=sys.stderr)
        detected = [_default_attrs()]
        needs_review = True

    try:
        image_url = _upload_processed_photo(processed_path, image_key)
    except Exception as e:
        if os.path.exists(processed_path):
            os.remove(processed_path)
        return [], f"could not upload photo to storage ({e})"

    saved_items = []
    for i, attrs in enumerate(detected):
        # All detected garments share the same source photo - we can't
        # crop individual items out without real image segmentation, so
        # each entry just points at the same image_key/image_url. Each
        # still gets its OWN item_code, since the code identifies the
        # garment, not the photo.
        item = {
            "id": uuid.uuid4().hex if i > 0 else item_id,
            "item_code": generate_item_code(),
            "profile_id": current_profile_id(),
            "image_key": image_key,
            "image_url": image_url,
            "type": attrs.get("type", "unknown"),
            "color": attrs.get("color", "unknown"),
            "description": attrs.get("description", ""),
            "warmth": attrs.get("warmth", 5),
            "zone": attrs.get("zone", "top"),
            "waterproof": attrs.get("waterproof", False),
            "occasions": attrs.get("occasions", ["casual"]),
            "in_laundry": False,
            "needs_review": needs_review,
            "added_at": datetime.utcnow().isoformat(),
        }
        save_item(item)
        saved_items.append(item)
    return saved_items, None


def _flash_upload_summary(file_count: int, saved_items: list, skipped: list, is_multi: bool) -> None:
    """Builds the flash message(s) for a batch upload of 1+ photos.
    Preserves the original, specific single-photo wording for the most
    common case (exactly one photo, nothing skipped) rather than always
    showing a generic aggregate message."""
    added_count = len(saved_items)
    needs_review_count = sum(1 for i in saved_items if i["needs_review"])

    if added_count == 0:
        if file_count == 1 and skipped:
            filename, reason = skipped[0]
            flash(f"Could not process {filename}: {reason}")
        else:
            flash(f"Could not process any of the {file_count} photo{'s' if file_count != 1 else ''}.")
        for filename, reason in skipped[:3]:
            flash(f"{filename}: {reason}")
        return

    if file_count == 1 and not skipped:
        if needs_review_count and is_multi:
            flash("Couldn't automatically split up that photo — saved it as one item, please edit its details below.")
        elif needs_review_count:
            flash("Auto-detection didn't work this time — this item was saved, please edit its details below.")
        elif is_multi and added_count > 1:
            flash(f"Added {added_count} items from that photo — they all share the same picture since it wasn't taken one-per-item.")
        else:
            item = saved_items[0]
            flash(f"Added {item['color']} {item['type']} (warmth {item['warmth']}/10, zone: {item['zone']}).")
        return

    parts = [f"Added {added_count} item{'s' if added_count != 1 else ''} from {file_count} photo{'s' if file_count != 1 else ''}"]
    if needs_review_count:
        parts.append(f"({needs_review_count} need review)")
    flash(" ".join(parts) + ".")
    if skipped:
        flash(f"{len(skipped)} photo{'s' if len(skipped) != 1 else ''} could not be processed.")
        for filename, reason in skipped[:3]:
            flash(f"{filename}: {reason}")


@app.route("/item/<item_id>/edit", methods=["POST"])
def edit_item(item_id):
    updates = {
        "type": request.form.get("type", "").strip() or "unknown item",
        "color": request.form.get("color", "").strip() or "unknown",
        "description": request.form.get("description", "").strip(),
        "zone": request.form.get("zone") if request.form.get("zone") in ("head", "top", "bottom", "feet", "dress") else "top",
        "warmth": max(1, min(10, int(request.form.get("warmth", 5) or 5))),
        "waterproof": request.form.get("waterproof") == "on",
        "occasions": [o for o in request.form.getlist("occasions") if o in VALID_OCCASIONS] or ["casual"],
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
    # Opt-in only (see DECISIONS.md ADR-017) - default is "separates",
    # today's unchanged behavior. Only an explicit style=dress switches
    # to the dress-based picker.
    style = request.args.get("style", "separates")
    if style not in ("separates", "dress"):
        style = "separates"

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

    if style == "dress":
        # Deliberately skips outfit_ai.py's AI coordination - combining
        # ONE dress with feet+head doesn't need the same multi-zone
        # coordination reasoning that top+bottom+feet+head benefits from.
        outfit = recommend.suggest_dress_outfit(filtered_closet, weather, bias=bias)
    else:
        try:
            outfit = suggest_outfit_ai(filtered_closet, weather, bias=bias)
        except OutfitAIFailed:
            outfit = suggest_outfit(filtered_closet, weather, bias=bias)
            outfit["notes"] = outfit.get("notes", []) + ["Styling suggestion unavailable right now — showing closest-warmth picks instead."]

    if occasion_note:
        outfit["notes"] = [occasion_note] + outfit.get("notes", [])

    # Try the photorealistic avatar first (see DECISIONS.md ADR-015);
    # fall back to the SVG mannequin on ANY failure so the suggestion
    # page never comes up without a visual — same "fail gracefully"
    # principle as the outfit_ai.py -> recommend.py fallback above.
    avatar_image = None
    try:
        avatar_bytes = generate_avatar_image(outfit["picks"])
        avatar_image = "data:image/jpeg;base64," + base64.b64encode(avatar_bytes).decode("ascii")
    except AvatarGenerationFailed:
        pass  # avatar_image stays None; template falls back to the mannequin

    return render_template(
        "suggest.html", weather=weather, outfit=outfit, city=city, occasion=occasion, style=style,
        weather_icon=weather_icon_svg(weather.get("condition", "")),
        mannequin=mannequin_svg(outfit["picks"]),
        avatar_image=avatar_image,
    )


@app.route("/log-outfit", methods=["POST"])
def log_outfit_route():
    target_warmth_raw = request.form.get("target_warmth")
    temp_c_raw = request.form.get("temp_c")
    entry = {
        "log_date": date.today().isoformat(),
        "profile_id": current_profile_id(),
        "occasion": request.form.get("occasion") if request.form.get("occasion") in VALID_OCCASIONS else "casual",
        "top_id": request.form.get("top_id") or None,
        "bottom_id": request.form.get("bottom_id") or None,
        "feet_id": request.form.get("feet_id") or None,
        "head_id": request.form.get("head_id") or None,
        # See DECISIONS.md ADR-017 - a dress-based outfit (opt-in via
        # /suggest?style=dress) has top_id/bottom_id both None and this
        # set instead, rather than trying to force it into either.
        "dress_id": request.form.get("dress_id") or None,
        "reasoning": request.form.get("reasoning") or None,
        # KNOWN_ISSUES.md #1b: this was previously stored as an uncast
        # form string into a numeric Postgres column - target_warmth
        # (below) was already correctly cast, temp_c was simply missed.
        "temp_c": float(temp_c_raw) if temp_c_raw else None,
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
        for zone in ("top", "bottom", "feet", "head", "dress"):
            item_id = entry.get(f"{zone}_id")
            entry[f"{zone}_item"] = closet_by_id.get(item_id) if item_id else None

    return render_template("history.html", entries=entries)


@app.route("/week")
def week():
    city = request.args.get("city", os.environ.get("DEFAULT_CITY", "Kochi,IN"))
    master_occasion = request.args.get("occasion", "any")

    # Per-day overrides come in as override_<date>=<occasion> query params
    # (e.g. override_2026-09-09=casual), set from each day card's own
    # small form on week.html.
    day_overrides = {
        key[len("override_"):]: value
        for key, value in request.args.items()
        if key.startswith("override_") and value and value != "default"
    }

    try:
        forecast_days = get_forecast(city, days=5)
    except Exception as e:
        flash(f"Could not fetch forecast: {e}")
        return redirect(url_for("index"))

    closet = load_closet(current_profile_id())
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    try:
        bias = compute_warmth_bias(get_history(current_profile_id()))
    except Exception:
        bias = 0

    occasion_note = None
    if day_overrides:
        # At least one day has its own occasion - plan day-by-day so each
        # can draw from a differently-filtered closet, while rotation
        # still spans the whole week.
        def get_closet_for_day(date_str):
            effective = day_overrides.get(date_str, master_occasion)
            return filter_by_occasion(closet, effective)

        planned_days = recommend.plan_days_per_occasion(get_closet_for_day, forecast_days, bias=bias)
    else:
        filtered_closet, occasion_note = filter_by_occasion(closet, master_occasion)
        planned_days = [
            {**d, "note": None} for d in recommend.plan_days(filtered_closet, forecast_days, bias=bias)
        ]

    days = []
    for planned_day in planned_days:
        days.append({
            "date": planned_day["date"],
            "weather": planned_day["weather"],
            "picks": planned_day["picks"],
            "weather_icon": weather_icon_svg(planned_day["weather"].get("condition", "")),
            "effective_occasion": day_overrides.get(planned_day["date"], master_occasion),
            "note": planned_day.get("note"),
        })

    return render_template(
        "week.html", days=days, city=city, occasion=master_occasion,
        occasion_note=occasion_note, day_overrides=day_overrides,
    )


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


@app.route("/swipe")
def swipe():
    """
    Renders the swipe feed's first card. Subsequent cards are fetched via
    /swipe/vote's JSON response, not full page loads — see that route's
    docstring and DECISIONS.md ADR-016 for why this is the app's one
    deliberate exception to the no-AJAX rule.
    """
    city = request.args.get("city", os.environ.get("DEFAULT_CITY", "Kochi,IN"))
    try:
        weather = get_current_weather(city)
    except Exception as e:
        flash(f"Could not fetch weather: {e}")
        return redirect(url_for("index"))

    closet = load_closet(current_profile_id())
    if not closet:
        flash("Your closet is empty — upload some clothes first.")
        return redirect(url_for("index"))

    try:
        bias = compute_warmth_bias(get_history(current_profile_id()))
    except Exception:
        bias = 0

    try:
        disliked = swipe_store.get_disliked_combo_keys(current_profile_id())
    except Exception:
        disliked = set()  # swipe history unavailable shouldn't block swiping

    combo = recommend.random_outfit_combo(closet, weather, bias=bias, exclude_keys=disliked)

    return render_template(
        "swipe.html", picks=combo["picks"], city=city,
        target_warmth=combo["target_warmth"],
    )


@app.route("/swipe/vote", methods=["POST"])
def swipe_vote():
    """
    JSON endpoint — the app's one deliberate exception to the no-AJAX
    rule (see DECISIONS.md ADR-016). A real swipe interaction needs to
    advance to the next card without a full page reload; every other
    interaction in this app stays a full-page-reload form and should
    continue to (don't generalize this pattern to other routes without
    the same explicit consideration ADR-016 documents).

    Expects JSON body: {"decision": "like"|"dislike"|"save",
    "picks": {"top": id_or_null, "bottom": ..., "feet": ..., "head": ...},
    "city": optional str}

    Returns JSON: {"picks": {zone: {id, type, color, image_url}_or_null},
    "target_warmth": int} for the NEXT card, or {"error": str} with a
    4xx/5xx status on failure — the frontend JS should show a small
    inline error and let the person retry rather than silently stalling.
    """
    data = request.get_json(silent=True) or {}
    decision = data.get("decision")
    if decision not in ("like", "dislike", "save"):
        return {"error": "invalid decision"}, 400

    picks_ids = data.get("picks", {})
    # Reconstruct minimal item stubs (just the id) so record_swipe's
    # existing picks-shape expectations (item.get("id")) are satisfied
    # without a second closet lookup — the swipe table only stores ids,
    # never full item data.
    picks = {
        zone: ({"id": picks_ids[zone]} if picks_ids.get(zone) else None)
        for zone in ("top", "bottom", "feet", "head")
    }

    try:
        swipe_store.record_swipe(current_profile_id(), picks, decision)
    except Exception as e:
        return {"error": f"could not record swipe: {e}"}, 500

    city = data.get("city") or os.environ.get("DEFAULT_CITY", "Kochi,IN")
    try:
        weather = get_current_weather(city)
        closet = load_closet(current_profile_id())
        bias = compute_warmth_bias(get_history(current_profile_id()))
        disliked = swipe_store.get_disliked_combo_keys(current_profile_id())
        combo = recommend.random_outfit_combo(closet, weather, bias=bias, exclude_keys=disliked)
    except Exception as e:
        return {"error": f"could not generate next combo: {e}"}, 500

    def _serialize(item):
        if not item:
            return None
        return {
            "id": item["id"],
            "type": item.get("type"),
            "color": item.get("color"),
            "image_url": item.get("image_url"),
        }

    return {
        "picks": {zone: _serialize(combo["picks"].get(zone)) for zone in ("top", "bottom", "feet", "head")},
        "target_warmth": combo["target_warmth"],
    }


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
