# Wardrobe AI — photo closet + weather-based outfit suggestions

Take a photo of a garment, Groq's vision model identifies it and scores its
warmth, and the app suggests what to wear based on today's weather.

## How it works

- `main.py` — Flask app, routes, and glue code.
- `groq_classifier.py` — sends an uploaded photo to Groq's free vision model
  (`qwen/qwen3.6-27b`) and gets back structured JSON: garment type, color,
  body zone, warmth score, waterproof flag. Retries automatically on rate
  limits; falls back to manual entry if classification ultimately fails.
- `image_utils.py` — resizes/compresses every photo before it's stored or
  classified, to keep storage and Groq usage tiny.
- `weather.py` — fetches current conditions from OpenWeatherMap's free API.
- `recommend.py` — simple rule-based logic that maps temperature to a target
  warmth score and picks the closest-matching item per body zone.
- `storage_supabase.py` / `closet_store.py` — a real Postgres `items` table
  plus Supabase Storage for photos, both on Supabase's free tier.
- `templates/` — the closet grid, upload form, and suggestion page.
- `.github/workflows/keep-alive.yml` — a scheduled ping that keeps the free
  Supabase project from auto-pausing (see "Why Supabase" below).

## 1. Get your free API keys and storage

1. **Groq** (photo classification): https://console.groq.com/keys — free,
   key starts with `gsk_...`.
2. **OpenWeatherMap** (weather lookup): https://openweathermap.org/api —
   free tier; new keys can take up to ~1 hour to activate.
3. **Supabase** (stores your photos *and* your closet data — no credit card
   required):
   - Sign up at https://supabase.com/dashboard and create a new project
     (pick any name/region; note the database password it asks you to set,
     though we won't need it directly).
   - Once the project's ready, go to the **SQL Editor** (left sidebar) →
     **New query**, paste this, and run it to create the closet table:
     ```sql
     create table items (
       id text primary key,
       image_key text not null,
       image_url text not null,
       type text,
       color text,
       warmth integer,
       zone text,
       waterproof boolean default false,
       needs_review boolean default false,
       added_at timestamptz default now()
     );
     ```
   - Go to **Storage** (left sidebar) → **New bucket**. Name it
     `wardrobe-photos`, and toggle it **Public**.
   - Go to **Project Settings** → **API**. Copy the **Project URL**
     (→ `SUPABASE_URL`) and the **service_role** secret key
     (→ `SUPABASE_KEY` — not the `anon` key; service_role is fine here
     since this app only ever runs server-side, never in a browser).

### Why Supabase, and the one thing to know about it

Render/Railway's free tier wipes local files on every restart, so photos
and closet data need to live somewhere persistent. Supabase gives real
Postgres + object storage for free with **no credit card**, which is why
it's used here over alternatives like Cloudflare R2 (which now requires
one even for its free tier).

The trade-off: a free Supabase project **auto-pauses after 7 days with
zero API activity**. Your data stays safe, but the app can't reach it
until you resume the project from the Supabase dashboard. The
`.github/workflows/keep-alive.yml` workflow in this repo pings a
`/keep-alive` endpoint every 3 days specifically to prevent that — see
step 8 below to enable it. If it does ever pause anyway (e.g. you disable
the workflow), just open the project on supabase.com/dashboard and click
**Restore project**.

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

Fill in `GROQ_API_KEY`, `OPENWEATHER_API_KEY`, `SUPABASE_URL`,
`SUPABASE_KEY`, and `SUPABASE_BUCKET` (defaults to `wardrobe-photos`,
matching the bucket name above).

## 4. Run it

```bash
python main.py
```

Visit **http://localhost:5000**.

## 5. Use it

1. Upload a photo of one garment at a time. Groq identifies it
   automatically — type, color, warmth score, zone, waterproof.
2. **If Groq is rate-limited or unavailable:** the app retries
   automatically (with backoff). If it still fails, the item is saved with
   a "needs review" badge and a quick manual-tag form right on the closet
   page — your photo is never lost.
3. Repeat across a few zones (top, bottom, feet — head is optional).
4. Go to **Suggest outfit** for a recommendation based on today's weather.

## 6. Push to GitHub

```bash
git remote add origin https://github.com/<your-username>/wardrobe-app.git
git branch -M main
git push -u origin main
```

`.env` is gitignored — your keys never get pushed.

## 7. Deploy

GitHub only hosts code — connect your repo to a host that actually runs
Python:

1. https://render.com → sign in with GitHub → **New +** → **Web Service**
   → select your repo.
2. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn main:app` (matches the `Procfile`)
3. Add all your `.env` values as environment variables in Render's
   dashboard.
4. Deploy. Every future `git push` auto-redeploys.

Note your deployed URL (e.g. `https://wardrobe-app-xxxx.onrender.com`) —
you'll need it for the next step.

## 8. Enable the keep-alive ping

This stops your Supabase project (and, as a bonus, a spun-down Render free
instance) from going idle:

1. On GitHub, go to your repo → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**.
2. Name it `APP_URL`, value = your deployed URL from step 7 (no trailing
   slash), e.g. `https://wardrobe-app-xxxx.onrender.com`.
3. That's it — `.github/workflows/keep-alive.yml` is already in the repo
   and will run automatically every 3 days. You can also trigger it
   manually any time from the repo's **Actions** tab → **Keep Supabase and
   Render awake** → **Run workflow**, useful for testing it works.

## 9. Database migrations

You already have an `outfit_history` table from before profiles existed
(with `log_date` alone as its primary key), so this needs to happen in
order: add the new columns first, backfill existing rows with a real
profile, and only THEN switch the primary key - a primary key can't
contain NULLs, so swapping it before backfilling would fail outright.

### Step 1 - run this now (safe regardless of existing data)

```sql
create table if not exists profiles (
  id text primary key,
  name text not null,
  gender text,
  created_at timestamptz default now()
);

alter table items add column if not exists occasion text default 'casual';
alter table items add column if not exists in_laundry boolean default false;
alter table items add column if not exists profile_id text;
alter table items add column if not exists occasions text[] default array['casual'];
alter table outfit_history add column if not exists profile_id text;

alter table items enable row level security;
alter table outfit_history enable row level security;
alter table profiles enable row level security;
```

`occasions` (plural, an array) replaces the older single-value `occasion`
column - a garment can now be tagged as multiple things at once (e.g. a
blazer as both "work" and "semi_formal"). The old `occasion` column is
left in place and untouched for backward compatibility; the app reads
`occasions` first and only falls back to it for rows saved before this
update.

### Step 2 - create your first profile through the app

Deploy the updated code and create a profile through the UI (it'll prompt
you automatically). Then find its id in Supabase's Table Editor →
`profiles` table.

### Step 3 - backfill your existing closet/history into that profile

```sql
update items set profile_id = '<paste-the-profile-id-here>' where profile_id is null;
update outfit_history set profile_id = '<paste-the-profile-id-here>' where profile_id is null;
```

### Step 4 - now it's safe to switch outfit_history's primary key

```sql
alter table outfit_history drop constraint if exists outfit_history_pkey;
alter table outfit_history add constraint outfit_history_pkey primary key (profile_id, log_date);
```

This makes `log_date` unique *per profile* instead of globally, so two
people can log an outfit on the same calendar date without colliding.

No new environment variables are needed anywhere in this section -
everything reuses your existing Supabase credentials.

## Notes and next steps

- **The recommendation logic is rule-based** (closest warmth score per
  zone), not ML — deliberately, so it's transparent and easy to tweak. If
  you want it to learn from feedback like the original ToWear project did
  (asking "what did you wear today, how did you feel"), that's a natural
  next feature.
- **Groq model names change.** If `qwen/qwen3.6-27b` ever stops working,
  check https://console.groq.com/docs/vision for the current vision model
  and update `GROQ_VISION_MODEL` in your `.env` (no code changes needed).
- **Rate limits:** Groq's and OpenWeatherMap's free tiers both have
  request limits — fine for personal use, but the retry/manual-fallback
  logic exists specifically to handle Groq hitting them gracefully.
- **Multiple garments in one photo:** the classifier prompt assumes one
  garment per photo for cleaner results. Scanning a whole wardrobe shelf
  in one shot would need a different prompt (returning a list of items)
  and matching image-handling changes — happy to help with that as a
  follow-up.
- **Supabase free tier limits:** 500MB database, 1GB file storage. At
  ~100-200KB per compressed photo, that's thousands of items before
  you'd need to upgrade — a personal wardrobe won't come close.
