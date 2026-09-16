"""
Generates a photorealistic image of a person wearing today's suggested
outfit, via Hugging Face's Inference Providers — specifically the
"hf-inference" provider, HF's own hosted free-tier service (this used to
be called "Inference API (serverless)" before HF's Inference Providers
launch, and its raw REST domain changed from api-inference.huggingface.co
to router.huggingface.co as part of that move).

This REVERSES ADR-008 (the abstract SVG mannequin) per explicit user
request — see DECISIONS.md ADR-015 for the full rationale.

Uses the `huggingface_hub` client library rather than raw HTTP requests,
matching this project's existing convention of using the vendor's own SDK
for model-hosting APIs that evolve over time (see groq_classifier.py /
outfit_ai.py's use of the `groq` package instead of raw requests to
Groq's REST API). HF's endpoint domain has already changed once since
this project started; pinning to the client library insulates this code
from another such change, since HF maintains the library against
whatever the current routing is.

IMPORTANT FIDELITY LIMITATION (documented in ADR-015 and KNOWN_ISSUES.md):
no free/no-credit-card text-to-image service can transplant an actual
PHOTOGRAPHED garment onto a generated person — that needs a specialized
garment-transfer model (IDM-VTON, OOTDiffusion, or a paid commercial try-on
API). This module instead builds a text prompt from each zone's detected
TYPE and COLOR — the exact same data mannequin.py already uses — so the
avatar is a photorealistic-looking approximation, not literal-garment
rendering.

Fails gracefully, per this project's established principle (CONTRIBUTING.md):
any error (missing token, network failure, model cold-start, rate limit,
malformed response) raises AvatarGenerationFailed so main.py can fall back
to the SVG mannequin — the suggestion page must never come up empty, exactly
like outfit_ai.py's OutfitAIFailed -> recommend.suggest_outfit() pattern.
"""
import os
import io
import time

from huggingface_hub import InferenceClient
try:
    from huggingface_hub.errors import HfHubHTTPError
except ImportError:  # older huggingface_hub versions exposed this under .utils instead
    from huggingface_hub.utils import HfHubHTTPError

HF_API_TOKEN = os.environ.get("HF_API_TOKEN")

# stabilityai/stable-diffusion-3-medium-diffusers is Hugging Face's own
# documented example model for the free "hf-inference" provider (see
# https://huggingface.co/docs/inference-providers/en/providers/hf-inference).
# Kept as an env var, same precedent as GROQ_VISION_MODEL/GROQ_TEXT_MODEL,
# since which models the free provider actually serves changes over time.
HF_AVATAR_MODEL = os.environ.get("HF_AVATAR_MODEL", "stabilityai/stable-diffusion-3-medium-diffusers")

# HF's free "hf-inference" provider cold-starts a model that hasn't been
# called recently (returns 503, sometimes 504 on gateway timeout) and can
# also rate-limit (429) - both worth a real retry, unlike a genuine 4xx
# (bad token, bad model name) which retrying won't fix.
MAX_RETRIES = 2
BASE_DELAY_SECONDS = 3
RETRYABLE_STATUS_CODES = (429, 503, 504)

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


def _status_code_of(error: HfHubHTTPError):
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) if response is not None else None


def generate_avatar_image(picks: dict) -> bytes:
    """Returns raw JPEG image bytes on success. Raises
    AvatarGenerationFailed on any failure."""
    if not HF_API_TOKEN:
        raise AvatarGenerationFailed("HF_API_TOKEN is not configured")

    prompt = _prompt_for_picks(picks)
    if not prompt:
        raise AvatarGenerationFailed("No items picked — nothing to render")

    client = InferenceClient(provider="hf-inference", api_key=HF_API_TOKEN)
    last_error = "unknown error"

    for attempt in range(MAX_RETRIES + 1):
        try:
            image = client.text_to_image(prompt, model=HF_AVATAR_MODEL)
            buf = io.BytesIO()
            image.save(buf, format="JPEG")
            return buf.getvalue()
        except HfHubHTTPError as e:
            status = _status_code_of(e)
            last_error = f"HTTP {status}: {e}"
            if status not in RETRYABLE_STATUS_CODES:
                # A genuine 4xx that isn't a rate limit (bad token, bad
                # model name, etc.) won't be fixed by retrying.
                raise AvatarGenerationFailed(last_error)
        except Exception as e:
            last_error = f"error: {e}"
        if attempt < MAX_RETRIES:
            time.sleep(BASE_DELAY_SECONDS * (2 ** attempt))

    raise AvatarGenerationFailed(f"Exhausted retries: {last_error}")
