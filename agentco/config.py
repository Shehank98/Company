"""Settings from environment. Secrets live in Railway env and never enter model context."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://agentco:agentco@localhost:5432/agentco"
    # Read-only roles on the live apps (SELECT only). Empty => connector disabled.
    adspot_ro_database_url: str = ""
    sidadiya_ro_database_url: str = ""

    # LLM: INTERNAL/CONFIDENTIAL data goes to paid Gemini only.
    llm_provider: str = "gemini"            # gemini | fake (tests/dev only)
    gemini_api_key: str = ""
    gemini_model_flash: str = "gemini-2.5-flash"   # ids come from env; names change
    gemini_model_pro: str = "gemini-2.5-pro"
    # USD per 1M tokens - VERIFY against current Gemini pricing and set in env.
    gemini_price_flash_in: float = 0.30
    gemini_price_flash_out: float = 2.50
    gemini_price_pro_in: float = 1.25
    gemini_price_pro_out: float = 10.0
    gemini_image_model: str = ""            # e.g. the Nano Banana model id; empty => flow_manual only
    gemini_image_price_usd: float = 0.04    # per image - VERIFY against current pricing
    # Phase 2
    adspot_write_database_url: str = ""     # optional: role with UPDATE on a few AdSpot status columns only
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_label: str = "AgentCo"
    undo_window_seconds: int = 30
    # YouTube Shorts
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token_adspot: str = ""      # one token per channel (sign in as that channel's owner)
    youtube_refresh_token_sidadiya: str = ""
    youtube_max_uploads_per_day: int = 5        # each upload costs 1600 of the default 10,000 daily quota units
    youtube_synthetic_disclosure: bool = True   # mark videos as containing AI-generated media
    veo_model: str = ""                         # Veo model id; empty => clips come from Google Flow by hand
    veo_price_per_second_usd: float = 0.40      # VERIFY against current pricing
    gemini_tts_model: str = ""                  # empty => no AI voiceover
    gemini_tts_voice: str = "Kore"
    gemini_tts_price_per_1k_chars_usd: float = 0.02   # VERIFY against current pricing
    max_video_mb: int = 100
    omniroute_enabled: bool = False         # Phase 3; PUBLIC-class tasks only

    # Owner auth
    google_client_id: str = ""
    owner_email: str = ""
    session_secret: str = "change-me"
    session_ttl_min: int = 120
    cookie_secure: bool = True

    # Web push
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:owner@example.com"

    upload_dir: str = "uploads"
    max_upload_mb: int = 10
    timezone: str = "Asia/Colombo"
    default_agent_budget_usd: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
