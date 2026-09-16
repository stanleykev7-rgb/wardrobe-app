"""
Generates a photorealistic image of a person wearing today's suggested
outfit, via the Hugging Face Inference API (text-to-image).

This REVERSES ADR-008 (the abstract SVG mannequin) per explicit user
request — see DECISIONS.md ADR-015 for the full rationale.

IMPORTANT FIDELITY LIMITATION (documented in ADR-015 and KNOWN_ISSUES.md):
no free/no-credit-card text-to-image service can transplant an actual
PHOTOGRAPHED garment onto a generated person — that needs a specialized
garment-transfer model (IDM-VTON, OOTDiffusion, or a paid commercial try-on
API). This module instead builds a text prompt from each zone's detected
TYPE and COLOR — the exact same data mannequin.py already uses — so the
avatar is a photorealistic-looking approximation, not literal-garment
rendering. This is a strictly-better-looking version of what the mannequin
already conceptually does, not a strictly-more-accurate one.

Fails gracefully, per this project's established principle (CONTRIBUTING.md):
any error (missing token, network failure, model cold-start, rate limit,
malformed response) raises AvatarGenerationFailed so main.py can fall back
to the SVG mannequin — the suggestion page must never come up empty, exactly
like outfit_ai.py's OutfitAIFailed -> recommend.suggest_outfit() pattern.
"""
import os
import time

import requests


HF_API_TOKEN = os.environ.get("HF_API_TOKEN")
HF_AVATAR_MODEL = os.environ.get("HF_AVATAR_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
HF_API_URL_TEMPLATE = "https://api-inference.huggingface.co/models/{model}"

# HF's shared free-tier inference endpoints cold-start a model that hasn't
# been called recently (returns 503 with an estimated_time), which is a
# fundamentally different situation than Groq's rate limits but still
# warrants a real retry rather than an immediate failure. Kept as its own
# named constants (not reusing groq_classifier's MAX_RETRIES/BASE_DELAY_SECONDS)
# since the two services' failure modes and appropriate backoff differ.
MAX_RETRIES = 2
BASE_DELAY_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 60

ZONE_LABELS = {"top": "top", "bottom": "bottom", "feet": "shoes", "head": "headwear"}


class AvatarGenerationFailed(Exception):
    """Raised on any failure to produce an avatar image. Callers must catch
    this and fall back to mannequin.mannequin_svg(picks) — never let a
    failed image-gen call take down the whole suggestion page."""


def _prompt_for_picks(picks: dict) -> str:
    """Builds a short text-to-image prompt from the same per-zone
    type/color data mannequin.py's _zone_fill() uses. An empty zone (no
    item picked) is simply omitted from the sentence, same spirit as the
    mannequin's dashed-placeholder-for-empty-zone behavior. Returns None
    if nothing was picked at all — nothing to render."""
    parts = []
    for zone, label in ZONE_LABELS.items():
        item = picks.get(zone)
        if not item:
            continue
        color = (item.get("color") or "").strip()
        garment_type = (item.get("type") or "").strip()
        words = [w for w in (color, garment_type) if w and w.lower() not in ("unknown", "unclassified item")]
        if words:
            parts.append(" ".join(words))
        else:
            parts.append(label)
    if not parts:
        return None
    outfit_desc = ", ".join(parts)
    return (
        f"Full body photo of a person standing in a neutral studio, wearing "
        f"{outfit_desc}. Realistic photography, soft studio lighting, plain "
        f"light grey background, fashion catalog style."
    )


def generate_avatar_image(picks: dict) -> bytes:
    """Returns raw image bytes (typically JPEG or PNG, whatever HF returns)
    on success. Raises AvatarGenerationFailed on any failure."""
    if not HF_API_TOKEN:
        raise AvatarGenerationFailed("HF_API_TOKEN is not configured")

    prompt = _prompt_for_picks(picks)
    if not prompt:
        raise AvatarGenerationFailed("No items picked — nothing to render")

    url = HF_API_URL_TEMPLATE.format(model=HF_AVATAR_MODEL)
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
    last_error = "unknown error"

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(
                url, headers=headers, json={"inputs": prompt}, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as e:
            last_error = f"network error: {e}"
            if attempt < MAX_RETRIES:
                time.sleep(BASE_DELAY_SECONDS * (2 ** attempt))
            continue

        content_type = response.headers.get("content-type", "")
        if response.status_code == 200 and content_type.startswith("image/"):
            return response.content

        # 503 = model loading/cold-starting on HF's shared free infra —
        # worth a real retry. 429 = rate limited — also worth a retry with
        # backoff. Anything else (401 bad token, 400 bad model name, etc.)
        # won't be fixed by retrying, so fail fast instead of burning time.
        if response.status_code in (503, 429):
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if attempt < MAX_RETRIES:
                time.sleep(BASE_DELAY_SECONDS * (2 ** attempt))
            continue

        raise AvatarGenerationFailed(f"HF API error {response.status_code}: {response.text[:200]}")

    raise AvatarGenerationFailed(f"Exhausted retries: {last_error}")
