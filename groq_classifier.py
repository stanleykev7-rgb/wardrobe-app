"""
Uses Groq's free-tier multimodal model to look at a photo of a garment
and return structured attributes we can store in the closet.
"""

import os
import json
import time
import base64

from groq import Groq

# Groq's current vision-capable model (as of mid-2026).
# Check https://console.groq.com/docs/vision if this ever stops working —
# Groq periodically retires/renames vision models.
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")

MAX_RETRIES = 2  # total attempts = MAX_RETRIES + 1
BASE_DELAY_SECONDS = 2

CLASSIFY_PROMPT = """You are looking at a photo of a single clothing item or accessory.
Identify it and respond with ONLY a JSON object (no markdown, no extra text) with these exact keys:

{
  "type": "short garment name, e.g. 'denim jacket', 'wool sweater', 'rain boots'",
  "color": "dominant color, e.g. 'navy blue'",
  "zone": "one of: head, top, bottom, feet",
  "warmth": integer from 1 (very light/summer) to 10 (very warm/heavy winter),
  "waterproof": true or false,
  "occasion": "one of: casual, work, formal, gym"
}

Rules:
- "zone" must be exactly one of: head, top, bottom, feet.
- If the item covers the torso (shirt, jacket, sweater, dress-top) use "top".
- If it covers the legs (pants, skirt, shorts) use "bottom".
- If it's worn on the head (hat, beanie, cap) use "head".
- If it's footwear (shoes, boots, sandals) use "feet".
- "warmth" should reflect how much insulation the item provides, not just its color.
- "occasion": "work" for business/office wear (blazers, dress shirts, slacks), "formal" for
  suits/dresses/formal shoes, "gym" for athletic wear (leggings, sneakers, sports tops),
  "casual" for everyday wear - default to "casual" if genuinely unclear.
- If you cannot clearly identify the garment, make your best guess rather than refusing.
"""


class ClassificationFailed(Exception):
    """Raised when Groq classification fails after retries. Callers should
    catch this specifically and fall back to manual entry, rather than
    losing the upload."""
    pass


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def classify_garment(image_path: str) -> dict:
    """
    Sends the image at image_path to Groq's vision model and returns a dict:
    {type, color, zone, warmth, waterproof}
    Retries on rate limits / transient errors. Raises ClassificationFailed
    (not the raw exception) if every attempt fails, so callers can catch
    one clear exception type and fall back to manual entry.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")

    client = Groq(api_key=api_key)

    ext = image_path.rsplit(".", 1)[-1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    b64_image = _encode_image(image_path)

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            completion = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": CLASSIFY_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64_image}"},
                            },
                        ],
                    }
                ],
                temperature=0.2,
                max_tokens=800,
                reasoning_effort="none",
                response_format={"type": "json_object"},
            )
            raw = completion.choices[0].message.content
            data = json.loads(raw)
            return _sanitize(data)

        except json.JSONDecodeError as e:
            # Model didn't return valid JSON - retrying rarely helps here,
            # but one retry with the same low temperature is cheap insurance.
            last_error = e
        except Exception as e:
            # Covers rate limits (429) and transient 5xx errors from Groq.
            # groq's SDK exceptions carry a status_code attribute when
            # they originate from an HTTP response.
            status = getattr(e, "status_code", None)
            last_error = e
            if status is not None and status != 429 and status < 500:
                # A genuine 4xx that isn't a rate limit (e.g. bad request,
                # auth failure) won't be fixed by retrying.
                break

        if attempt < MAX_RETRIES:
            time.sleep(BASE_DELAY_SECONDS * (2 ** attempt))

    raise ClassificationFailed(str(last_error))


def _sanitize(data: dict) -> dict:
    zone = str(data.get("zone", "top")).lower()
    if zone not in ("head", "top", "bottom", "feet"):
        zone = "top"

    occasion = str(data.get("occasion", "casual")).lower()
    if occasion not in ("casual", "work", "formal", "gym"):
        occasion = "casual"

    try:
        warmth = int(data.get("warmth", 5))
    except (TypeError, ValueError):
        warmth = 5
    warmth = max(1, min(10, warmth))

    return {
        "type": str(data.get("type", "unknown item")),
        "color": str(data.get("color", "unknown")),
        "zone": zone,
        "warmth": warmth,
        "waterproof": bool(data.get("waterproof", False)),
        "occasion": occasion,
    }
