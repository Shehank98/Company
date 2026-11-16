"""Optional automatic clips through the OFFICIAL Veo API (paid, model id from env). Google Flow itself has no
public API, so the default path is manual: the Director writes the Flow prompt and you upload the clip.
Generation is asynchronous: start() returns an operation name, poll() is called again later (job requeue)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import get_settings
from ..llm.base import LLMError

CLIP_SECONDS = 8
_fake_polls: dict[str, int] = {}


def available() -> bool:
    s = get_settings()
    return s.llm_provider == "fake" or bool(s.gemini_api_key and s.veo_model)


def start(prompt: str) -> str:
    s = get_settings()
    if s.llm_provider == "fake":
        op = f"fake-op-{len(_fake_polls) + 1}"
        _fake_polls[op] = 0
        return op
    if not (s.gemini_api_key and s.veo_model):
        raise LLMError("Veo needs GEMINI_API_KEY and VEO_MODEL")
    from google import genai
    from google.genai import types
    try:
        # ponytail: Veo request/poll shapes follow the google-genai docs; not verified against a live key yet.
        op = genai.Client(api_key=s.gemini_api_key).models.generate_videos(
            model=s.veo_model, prompt=prompt, config=types.GenerateVideosConfig(aspect_ratio="9:16", number_of_videos=1))
        return op.name
    except Exception as e:
        raise LLMError(f"Veo start failed: {type(e).__name__}") from e


def poll(op_name: str, out_path: Path) -> str:
    """Returns 'pending' | 'ready' (file written) . Raises LLMError on failure."""
    s = get_settings()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if s.llm_provider == "fake":
        _fake_polls[op_name] = _fake_polls.get(op_name, 0) + 1
        if _fake_polls[op_name] < 2:
            return "pending"
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=720x1280:r=30:d=3", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(out_path)],
                       check=True, timeout=60)
        return "ready"
    from google import genai
    from google.genai import types
    try:
        client = genai.Client(api_key=s.gemini_api_key)
        op = client.operations.get(types.GenerateVideosOperation(name=op_name))
        if not op.done:
            return "pending"
        if getattr(op, "error", None):
            raise LLMError("Veo reported an error for this clip")
        vid = op.response.generated_videos[0]
        client.files.download(file=vid.video)
        vid.video.save(str(out_path))
        return "ready"
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"Veo poll failed: {type(e).__name__}") from e
