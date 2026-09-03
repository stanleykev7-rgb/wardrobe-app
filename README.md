# Wardrobe AI — photo closet + weather-based outfit suggestions

Take a photo of a garment, Groq's vision model identifies it and scores its
warmth, and the app suggests what to wear based on today's weather.

## How it works

- `main.py` — Flask app, routes, and glue code.
- `groq_classifier.py` — sends an uploaded photo to Groq's free vision model
  (`qwen/qwen3.6-27b`) and gets back structured JSON: garment type, color,
  body zone, warmth score, waterproof flag.
- `weather.py` — fetches current conditions from OpenWeatherMap's free API.
- `recommend.py` — simple rule-based logic that maps temperature to a target
  warmth score and picks the closest-matching item per body zone.
- `closet_store.py` — stores your closet as a local `closet.json` file (no
  database setup needed to get started).
- `templates/` — the three HTML pages (closet grid, upload form, suggestion).

## 1. Get your free API keys

1. **Groq** (does the photo classification): go to
   https://console.groq.com/keys, sign up (free), and create a key. It
   starts with `gsk_...`.
2. **OpenWeatherMap** (does the weather lookup): go to
   https://openweathermap.org/api, sign up (free tier), and grab your API
   key from your account page. Note: new OpenWeatherMap keys can take up to
   ~1 hour to activate.

## 2. Set up the project

```bash
# Create the project folder and initialize git
mkdir wardrobe-app
cd wardrobe-app
git init

# (copy in the files from this scaffold, or clone if you've pushed it to
# your own GitHub repo)

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## 3. Configure your environment

```bash
cp .env.example .env
```

Then open `.env` and fill in:

```
GROQ_API_KEY=gsk_your_real_key
OPENWEATHER_API_KEY=your_real_key
DEFAULT_CITY=Kochi,IN
```

(`DEFAULT_CITY` uses OpenWeatherMap's `City,CountryCode` format — change it
to wherever you are.)

## 4. Run it

```bash
python main.py
```

Visit **http://localhost:5000** in your browser.

## 5. Use it

1. On the home page, upload a photo of one garment at a time (a jacket, a
   pair of jeans, shoes, a hat, etc). Groq will identify the type, color,
   warmth score, body zone, and whether it looks waterproof — this happens
   automatically, no manual tagging needed.
2. Repeat for a handful of items across different zones (top, bottom, feet
   — head is optional) so there's something to recommend from.
3. Go to **Suggest outfit**, optionally change the city, and you'll get a
   recommended item per zone based on today's actual weather.

## 6. Push to GitHub

```bash
# Create a new empty repo on github.com first (no README/license), then:
git remote add origin https://github.com/<your-username>/wardrobe-app.git
git branch -M main
git push -u origin main
```

Your `.env` file is already excluded via `.gitignore`, so your API keys
won't be pushed. Never commit `.env` — only `.env.example` (which has no
real keys) should go to GitHub.

## 7. Deploy so it's actually running online (not just local)

GitHub only hosts your code — it doesn't run Python servers (GitHub Pages
serves static sites only, not Flask apps). To get a live URL, connect your
GitHub repo to a hosting platform. **Render** is the easiest free option:

1. Go to https://render.com and sign up (you can sign in with GitHub).
2. Click **New +** → **Web Service**.
3. Connect your GitHub account and select your `wardrobe-app` repo.
4. Render will detect it's Python. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn main:app` (already in the `Procfile`,
     Render usually picks this up automatically)
5. Under **Environment**, add your environment variables (same as `.env`):
   - `GROQ_API_KEY`
   - `OPENWEATHER_API_KEY`
   - `FLASK_SECRET_KEY`
   - `DEFAULT_CITY`
6. Click **Create Web Service**. Render will build and deploy — you'll get
   a live URL like `https://wardrobe-app-xxxx.onrender.com`.
7. From now on, every `git push` to your GitHub repo auto-redeploys.

**Railway** (https://railway.app) works almost identically — connect
GitHub, it auto-detects the Procfile, add the same environment variables.

### Important: free-tier storage is not permanent

Render's and Railway's free tiers use an **ephemeral filesystem** — any
files written while the app is running (your `closet.json` and the photos
in `static/uploads/`) get wiped whenever the app restarts or redeploys
(free tiers also spin down after inactivity and restart on the next
request). That's fine for trying things out, but not for a closet you
actually want to keep.

For real persistence later, you'd swap:
- `closet.json` → a hosted database (Render's free Postgres tier, or
  Supabase's free tier)
- `static/uploads/` → object storage (Supabase Storage, Cloudflare R2, or
  AWS S3 all have free tiers)

Both are drop-in replacements for `closet_store.py` and the file-saving
logic in `main.py` — the rest of the app (Groq classification, weather,
recommendation logic) doesn't need to change. Happy to help wire either of
those up when you're ready to make it permanent.

## Notes and next steps

- **Storage is a flat JSON file** (`closet.json`) to keep this simple. If
  you outgrow that, swap `closet_store.py` for SQLite or Postgres — the
  rest of the app doesn't need to change.
- **The recommendation logic is rule-based** (closest warmth score per
  zone), not ML — deliberately, so it's transparent and easy to tweak. If
  you want it to learn from your feedback like the original ToWear project
  did (asking "what did you wear today, how did you feel"), that's a
  natural next feature: log actual wears against that day's weather and
  adjust target warmth over time.
- **Groq model names change.** If `qwen/qwen3.6-27b` ever stops working,
  check https://console.groq.com/docs/vision for the current vision model
  and update `GROQ_VISION_MODEL` in your `.env` (no code changes needed).
- **Rate limits:** Groq's free tier and OpenWeatherMap's free tier both
  have request limits. Fine for personal use; check their docs if you hit
  a 429 error.
- **Multiple garments in one photo:** the classifier prompt assumes one
  garment per photo for cleaner results. If you want to scan a whole
  wardrobe shelf in one shot, you'd need to change the prompt to return a
  list of items and split the image handling accordingly — happy to help
  with that as a follow-up.
