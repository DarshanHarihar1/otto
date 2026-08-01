import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    linq_api_token: str
    linq_webhook_secret: str
    openai_api_key: str
    prava_api_key: str
    prava_base_url: str
    supabase_db_url: str
    demo_user_phone: str
    confidence_threshold: float


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"missing required env var {key}")
    return value


settings = Settings(
    linq_api_token=_require("LINQ_API_TOKEN"),
    # linq_webhook_secret and prava_api_key are NOT _require()'d: the webhook
    # secret only exists once Task 4 of this same phase creates the Linq
    # subscription, and the Prava key is obtained separately (Phase 4). Both
    # default to "" so config loading — and every task before the one that
    # actually needs each value — isn't blocked on a secret that can't exist yet.
    linq_webhook_secret=os.environ.get("LINQ_WEBHOOK_SECRET", ""),
    openai_api_key=_require("OPENAI_API_KEY"),
    prava_api_key=os.environ.get("PRAVA_API_KEY", ""),
    prava_base_url=os.environ.get("PRAVA_BASE_URL", "https://sandbox.api.prava.space"),
    supabase_db_url=_require("SUPABASE_DB_URL"),
    demo_user_phone=_require("DEMO_USER_PHONE"),
    confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.80")),
)
