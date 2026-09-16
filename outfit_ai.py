"""
Coordinated outfit suggestion using Groq's text reasoning (not vision this
time - no image involved here, just structured closet data).

Where recommend.py picks each zone independently by closest warmth score,
this module asks the model to reason across a short-list of weather-
appropriate candidates per zone and pick ones that actually go together
(color/style), plus explain why in a sentence.

If this fails for any reason (rate limit, bad response, etc.), main.py
falls back to recommend.suggest_outfit - this module never needs to be
bulletproof on its own.
"""

import os
import json

from groq import Groq

import recommend

TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b"))

CANDIDATES_PER_ZONE = 4

PROMPT_TEMPLATE = """You are a helpful stylist choosing one outfit for today.

Weather: {temp_c}°C, feels like {feels_like_c}°C, {condition}{rain_note}.

Here are the available clothing options, grouped by body zone. Each item has
an id, type, color, and warmth score (1-10, higher = warmer):

{candidates_json}

Pick ONE item id per zone (or null if a zone has no good option) that:
1. Suits today's weather (warmth appropriate, waterproof if it's raining).
2. Looks coordinated together as a single outfit (colors/styles that work
   well as a combination, not just individually weather-appropriate).

Then rate the resulting outfit's style on a scale of 1-10 (be genuine, not
just generous - a plain but functional outfit might be a 6, a genuinely
well-coordinated one an 8+) and write a short, fun verdict line about it,
like a witty friend giving their honest opinion - not a dry technical
assessment.

Respond with ONLY a JSON object, no markdown, no extra text:
{{
  "top": "<item id or null>",
  "bottom": "<item id or null>",
  "feet": "<item id or null>",
  "head": "<item id or null>",
  "reasoning": "one short sentence on why these work together for today",
  "style_score": <integer 1-10>,
  "verdict": "one short, fun sentence giving your honest take on the outfit's style"
}}
"""


class OutfitAIFailed(Exception):
    """Raised when the AI outfit suggestion fails - callers should fall
    back to recommend.suggest_outfit."""
    pass


def _build_candidates(closet: list, weather: dict, bias: int = 0) -> dict:
    temp = weather.get("feels_like_c", weather.get("temp_c", 20))
    target = recommend.target_warmth(temp, bias)
    need_waterproof = weather.get("rain", False)

    candidates = {}
    for zone in recommend.ZONES_REQUIRED + recommend.ZONES_OPTIONAL:
        items = recommend.candidates_for_zone(closet, zone, target, need_waterproof, limit=CANDIDATES_PER_ZONE)
        candidates[zone] = items
    return candidates


def _candidates_to_prompt_json(candidates: dict) -> str:
    slim = {}
    for zone, items in candidates.items():
        slim[zone] = [
            {"id": i["id"], "type": i.get("type"), "color": i.get("color"), "warmth": i.get("warmth")}
            for i in items
        ]
    return json.dumps(slim, indent=2)


def suggest_outfit_ai(closet: list, weather: dict, bias: int = 0) -> dict:
    """
    Returns the same shape as recommend.suggest_outfit:
    {"picks": {zone: item_or_None}, "target_warmth": int, "notes": [str], "reasoning": str}
    Raises OutfitAIFailed if the model call or response parsing fails.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise OutfitAIFailed("GROQ_API_KEY is not set.")

    candidates = _build_candidates(closet, weather, bias)

    # If every zone has zero candidates, there's nothing for the model to
    # choose between - no point calling the API.
    if not any(candidates.values()):
        raise OutfitAIFailed("No candidate items available in any zone.")

    rain_note = " (rain expected)" if weather.get("rain") else ""
    prompt = PROMPT_TEMPLATE.format(
        temp_c=weather.get("temp_c", "?"),
        feels_like_c=weather.get("feels_like_c", "?"),
        condition=weather.get("condition", "unknown"),
        rain_note=rain_note,
        candidates_json=_candidates_to_prompt_json(candidates),
    )

    try:
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=900,
            reasoning_effort="none",
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content
        data = json.loads(raw)
    except Exception as e:
        raise OutfitAIFailed(str(e))

    # Resolve returned ids back to full item dicts, validating they're
    # actually items we offered as candidates (never trust model output
    # blindly - it could hallucinate an id).
    all_candidates_by_id = {i["id"]: i for items in candidates.values() for i in items}

    picks = {}
    for zone in recommend.ZONES_REQUIRED + recommend.ZONES_OPTIONAL:
        picked_id = data.get(zone)
        picks[zone] = all_candidates_by_id.get(picked_id) if picked_id else None

    notes = []
    for zone in recommend.ZONES_REQUIRED:
        if picks[zone] is None and candidates.get(zone):
            # The model had options but chose none/an invalid id - fall
            # back to the closest-warmth candidate for that zone rather
            # than leaving it empty.
            picks[zone] = candidates[zone][0]
        elif picks[zone] is None:
            notes.append(f"No {zone} items in your closet yet — add some for full suggestions.")

    temp = weather.get("feels_like_c", weather.get("temp_c", 20))
    target = recommend.target_warmth(temp, bias)

    if weather.get("rain") and not any(
        picks[z] and picks[z].get("waterproof") for z in recommend.ZONES_REQUIRED if picks[z]
    ):
        notes.append("Rain is expected but no waterproof items were found — consider bringing an umbrella.")

    reasoning = str(data.get("reasoning", "")).strip() or None

    try:
        style_score = int(data.get("style_score"))
        style_score = max(1, min(10, style_score))
    except (TypeError, ValueError):
        style_score = None

    verdict = str(data.get("verdict", "")).strip() or None

    return {
        "picks": picks,
        "target_warmth": target,
        "notes": notes,
        "reasoning": reasoning,
        "style_score": style_score,
        "verdict": verdict,
    }
