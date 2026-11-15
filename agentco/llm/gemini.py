"""Paid Gemini API provider. Model ids and prices come from env. A Claude provider can be added
later by implementing the same Provider protocol in a sibling module and registering it in router."""
from __future__ import annotations

from ..config import get_settings
from .base import LLMError, LLMResult


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        s = get_settings()
        if not s.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        from google import genai  # imported lazily so tests need no key
        self._client = genai.Client(api_key=s.gemini_api_key)

    def complete(self, *, system: str, prompt: str, tier: str, json_mode: bool) -> LLMResult:
        from google.genai import types
        s = get_settings()
        model = s.gemini_model_pro if tier == "pro" else s.gemini_model_flash
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else "text/plain",
            temperature=0.2,
        )
        try:
            r = self._client.models.generate_content(model=model, contents=prompt, config=cfg)
        except Exception as e:  # network / quota / safety
            raise LLMError(f"gemini call failed: {type(e).__name__}") from e
        u = r.usage_metadata
        tin = int(getattr(u, "prompt_token_count", 0) or 0)
        tout = int(getattr(u, "candidates_token_count", 0) or 0)
        pin, pout = ((s.gemini_price_pro_in, s.gemini_price_pro_out) if tier == "pro"
                     else (s.gemini_price_flash_in, s.gemini_price_flash_out))
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        return LLMResult(r.text or "", self.name, model, "direct", tin, tout, cost)
