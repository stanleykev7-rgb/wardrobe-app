"""
Uses Groq's free-tier multimodal model to look at a photo of a garment
and return structured attributes we can store in the closet.
"""

import os
import json
import base64

from groq import Groq

# Groq's current vision-capable model (as of mid-2026).
# Check https://console.groq.com/docs/vision if this ever stops working —
# Groq periodically retires/renames vision models.
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")

CLASSIFY_PROMPT = """You are looking at a photo of a single clothing item or accessory.
Identify it and respond with ONLY a JSON object (no markdown, no extra text) with these exact keys:

{
  "type": "short garment name, e.g. 'denim jacket', 'wool sweater', 'rain boots'",
  "color": "dominant color, e.g. 'navy blue'",
  "zone": "one of: head, top, bottom, feet",
  "warmth": integer from 1 (very light/summer) to 10 (very warm/heavy winter),
  "waterproof": true or false
}

Rules:
- "zone" must be exactly one of: head, top, bottom, feet.
- If the item covers the torso (shirt, jacket, sweater, dress-top) use "top".
- If it covers the legs (pants, skirt, shorts) use "bottom".
- If it's worn on the head (hat, beanie, cap) use "head".
- If it's footwear (shoes, boots, sandals) use "feet".
- "warmth" should reflect how much insulation the item provides, not just its color.
- If you cannot clearly identify the garment, make your best guess rather than refusing.
"""


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def classify_garment(image_path: str) -> dict:
    """
    Sends the image at image_path to Groq's vision model and returns a dict:
    {type, color, zone, warmth, waterproof}
    Raises an exception if the API call fails or the model doesn't return valid JSON.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")

    client = Groq(api_key=api_key)

    ext = image_path.rsplit(".", 1)[-1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    b64_image = _encode_image(image_path)

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
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content
    data = json.loads(raw)

    # Basic sanitation so a slightly-off model response never crashes the app
    zone = str(data.get("zone", "top")).lower()
    if zone not in ("head", "top", "bottom", "feet"):
        zone = "top"

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
    }
