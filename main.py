import os
import json
import uuid
from datetime import datetime

from flask import Flask, request, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from groq_classifier import classify_garment
from weather import get_current_weather
from recommend import suggest_outfit
from closet_store import load_closet, save_item

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


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

    # Save the image with a unique name so uploads never collide
    ext = file.filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(save_path)

    # Ask Groq's vision model what the garment is
    try:
        attrs = classify_garment(save_path)
    except Exception as e:
        flash(f"Classification failed: {e}")
        return redirect(url_for("index"))

    item = {
        "id": uuid.uuid4().hex,
        "image": unique_name,
        "type": attrs.get("type", "unknown"),
        "color": attrs.get("color", "unknown"),
        "warmth": attrs.get("warmth", 5),
        "zone": attrs.get("zone", "top"),
        "waterproof": attrs.get("waterproof", False),
        "added_at": datetime.utcnow().isoformat(),
    }
    save_item(item)

    flash(f"Added {item['color']} {item['type']} (warmth {item['warmth']}/10, zone: {item['zone']}).")
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


if __name__ == "__main__":
    # This block only runs for local development (python main.py).
    # In production, gunicorn imports `app` directly (see Procfile) and
    # this block is never executed.
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(debug=debug_mode, port=int(os.environ.get("PORT", 5000)))
