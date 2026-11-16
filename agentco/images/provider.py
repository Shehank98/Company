"""ImageProvider: two modes, chosen per pack.
  flow_manual  - no generation. The Designer writes the brief + exact prompt; the owner makes the image
                 in Google Flow and uploads it (there is no official Flow API; unofficial wrappers are not used).
  gemini_image - Gemini image model through the paid API (model id from env; names change).
Prompts contain only public facts + the post topic, never customer data."""
from __future__ import annotations

import base64
from dataclasses import dataclass

from ..config import get_settings
from ..llm.base import LLMError

# 1x1 transparent PNG used by the offline fake provider
_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@dataclass
class ImageResult:
    data: bytes
    ext: str
    provider: str
    model: str
    cost_usd: float


def available() -> bool:
    s = get_settings()
    return s.llm_provider == "fake" or bool(s.gemini_api_key and s.gemini_image_model)


def generate(prompt: str, aspect: str = "1:1") -> ImageResult:
    s = get_settings()
    if s.llm_provider == "fake":
        return ImageResult(_PNG, ".png", "fake", "fake-image", 0.001)
    if not (s.gemini_api_key and s.gemini_image_model):
        raise LLMError("gemini_image needs GEMINI_API_KEY and GEMINI_IMAGE_MODEL")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=s.gemini_api_key)
    try:
        # ponytail: image_config/aspect_ratio support depends on the model; not verified against a live key yet.
        r = client.models.generate_content(
            model=s.gemini_image_model, contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"],
                                               image_config=types.ImageConfig(aspect_ratio=aspect)))
    except Exception as e:
        raise LLMError(f"image generation failed: {type(e).__name__}") from e
    for part in (r.candidates[0].content.parts if r.candidates else []):
        if getattr(part, "inline_data", None) and part.inline_data.data:
            mime = part.inline_data.mime_type or "image/png"
            return ImageResult(part.inline_data.data, ".jpg" if "jpeg" in mime else ".png", "gemini",
                               s.gemini_image_model, s.gemini_image_price_usd)
    raise LLMError("model returned no image")
