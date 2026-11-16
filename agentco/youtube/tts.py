"""Voiceover via the paid Gemini TTS API (model id from env). Fake provider for tests."""
from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..llm.base import LLMError


@dataclass
class TTSResult:
    seconds: float
    cost_usd: float
    provider: str
    model: str


def available() -> bool:
    s = get_settings()
    return s.llm_provider == "fake" or bool(s.gemini_api_key and s.gemini_tts_model)


def _write_wav(path: Path, pcm: bytes, rate: int) -> float:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return len(pcm) / (rate * 2)


def synthesize(text: str, out_path: Path) -> TTSResult:
    s = get_settings()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if s.llm_provider == "fake":
        secs = max(1.5, len(text.split()) / 2.6)
        rate = 16000
        pcm = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(secs * rate)))
        return TTSResult(_write_wav(out_path, pcm, rate), 0.001, "fake", "fake-tts")
    if not (s.gemini_api_key and s.gemini_tts_model):
        raise LLMError("voiceover needs GEMINI_API_KEY and GEMINI_TTS_MODEL")
    from google import genai
    from google.genai import types
    try:
        # ponytail: speech_config shape follows the google-genai TTS docs; not verified against a live key yet.
        r = genai.Client(api_key=s.gemini_api_key).models.generate_content(
            model=s.gemini_tts_model, contents=text,
            config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=s.gemini_tts_voice)))))
        pcm = r.candidates[0].content.parts[0].inline_data.data       # 24 kHz, 16-bit mono PCM
    except Exception as e:
        raise LLMError(f"voiceover failed: {type(e).__name__}") from e
    secs = _write_wav(out_path, pcm, 24000)
    return TTSResult(secs, len(text) / 1000 * s.gemini_tts_price_per_1k_chars_usd, "gemini", s.gemini_tts_model)
