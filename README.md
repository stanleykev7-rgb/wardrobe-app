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

## 1. Get your free API keys and storage

1. **Groq** (does the photo classification): go to
   https://console.groq.com/keys, sign up (free), and create a key. It
   starts with `gsk_...`.
2. **OpenWeatherMap** (does the weather lookup): go to
   https://openweathermap.org/api, sign up (free tier), and grab your API
   key from your account page. Note: new OpenWeatherMap keys can take up to
   ~1 hour to activate.
3. **Cloudflare R2** (stores your photos *and* your closet data, so
   everything survives restarts/redeploys — see "Why R2" below):
   - Sign up at https://dash.cloudflare.com (free, no card required for R2's
     free tier).
   - Go to **R2 Object Storage** → **Create bucket**. Name it anything,
     e.g. `wardrobe-app`.
   - Open the bucket → **Settings** → under **Public Access**, enable the
     `r2.dev` subdomain (or connect a custom domain if you have one). Copy
     that public URL — you'll need it as `R2_PUBLIC_URL`.
   - Go to **R2** → **Manage API Tokens** → **Create API Token**. Give it
     **Object Read & Write** permission, scoped to your bucket. Copy the
     **Access Key ID**, **Secret Access Key**, and note your **Account ID**
     (shown on the R2 overview page).

### Why R2 instead of local disk, and why no separate database?

Render/Railway's free tier wipes local files on every restart. R2 gives
you **10GB of storage free, forever, with zero egress fees** — plenty for
thousands of garment photos (each photo is resized/compressed to ~100-200KB
before storage, see `image_utils.py`). Since this is a single-user hobby
app, the closet's metadata (`closet.json`) is stored as one JSON object
inside the same R2 bucket instead of standing up a separate database —
one less service to configure. If you ever need multi-user concurrent
writes, swap `closet_store.py` for a real database; nothing else changes.

## 2. Set up the project

```bash
mkdir wardrobe-app
cd wardrobe-app
git init

python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## 3. Configure your environment

```bash
cp .env.example .env
```

Then fill in `GROQ_API_KEY`, `OPENWEATHER_API_KEY`, and the R2 values:
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET_NAME`, `R2_PUBLIC_URL`.

## 4. Run it

```bash
python main.py
```

Visit **http://localhost:5000**.

## 5. Use it

1. Upload a photo of one garment at a time. Groq identifies it
   automatically — type, color, warmth score, zone, waterproof.
2. **If Groq is rate-limited or unavailable:** the app retries automatically
   (with backoff) up to 2 times. If it still fails, your photo is **not
   lost** — the item is saved with a "needs review" badge and a small form
   right on the closet page so you can tag it manually in a few seconds.
3. Repeat across a few zones (top, bottom, feet — head is optional).
4. Go to **Suggest outfit** for a recommendation based on today's weather.

## 6. Push to GitHub

```bash
git remote add origin https://github.com/<your-username>/wardrobe-app.git
git branch -M main
git push -u origin main
```

`.env` is gitignored — your keys never get pushed. Only `.env.example`
(no real keys) should go to GitHub.

## 7. Deploy

GitHub only hosts code — it doesn't run Python servers. Connect your repo
to a host that does:

1. https://render.com → sign in with GitHub → **New +** → **Web Service**
   → select your repo.
2. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn main:app` (matches the `Procfile`)
3. Add all your `.env` values as environment variables in Render's
   dashboard (never upload `.env` itself).
4. Deploy. Every future `git push` auto-redeploys.

Railway (https://railway.app) works almost identically.

**With R2 in place, your data now survives restarts** — this was the main
gap in the earlier local-storage version.

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
