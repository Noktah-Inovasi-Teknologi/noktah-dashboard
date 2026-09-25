"""Configuration, from environment variables only (docker-compose passes them)."""
from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_API_", extra="ignore")

    # postgresql://user:pass@postgres:5432/noktah_dashboard
    database_url: str

    # Cloudflare Access team domain; its signing keys live at /cdn-cgi/access/certs.
    access_team_domain: str = "shy-bird-c2c5.cloudflareaccess.com"

    # Application Audience (AUD) tags, from Zero Trust > Access > Applications.
    # api_aud: the application in front of this API (hub-api.noktah.co), which admits
    #          only the Hub Worker's service token.
    # hub_aud: the Noktah Hub application (hub.noktah.co), whose login identifies the user.
    # min_length: an unset AUD must stop the API at startup, not run it wide open or
    # quietly rejecting everyone.
    access_api_aud: str = Field(min_length=1)
    access_hub_aud: str = "0aae0ce6863685fab39723bffab5cb0580dbd13f491e3c2807817255b9d6bb02"

    # Prefect → /internal/* (Docker network only). Empty = internal routes disabled.
    internal_token: str = ""

    # AI (spec 008). Models are configuration per call site (constitution II).
    openrouter_api_key: str = Field(default="", validation_alias=AliasChoices("OPENROUTER_API_KEY"))
    intake_model: str = Field(default="xiaomi/mimo-v2.5", validation_alias=AliasChoices("HUB_INTAKE_MODEL"))
    ai_monthly_cap_usd: float = Field(default=5.0, validation_alias=AliasChoices("HUB_AI_MONTHLY_CAP_USD"))
    ai_timeout_seconds: float = 90.0

    # Google (Docs intake, sheet copy). Same OAuth refresh-token pattern as Prefect.
    google_client_id: str = Field(default="", validation_alias=AliasChoices("GOOGLE_CLIENT_ID"))
    google_client_secret: str = Field(default="", validation_alias=AliasChoices("GOOGLE_CLIENT_SECRET"))
    google_refresh_token: str = Field(default="", validation_alias=AliasChoices("GOOGLE_REFRESH_TOKEN"))
    clients_spreadsheet_id: str = "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"

    # Slack incoming webhooks (read by name; see noktah_brands.slack_managerial_env).
    slack_automation_noktah: str = Field(default="", validation_alias=AliasChoices("SLACK_AUTOMATION_NOKTAH"))

    # Prefect API, only to pause roster-sync after the one-time import.
    prefect_api_url: str = Field(default="http://prefect:4200/api", validation_alias=AliasChoices("PREFECT_API_URL"))

    # Card definition files (mounted read-only from ./config/hub).
    card_definition_dir: str = "/app/config/hub"
    # Accepted models per case and their rotation (mounted read-only from ./config/ai).
    ai_models_file: str = "/app/config/ai/models.yaml"

    public_hub_url: str = "https://hub.noktah.co"

    @property
    def access_certs_url(self) -> str:
        return f"https://{self.access_team_domain}/cdn-cgi/access/certs"

    @property
    def access_issuer(self) -> str:
        return f"https://{self.access_team_domain}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def env(name: str) -> Optional[str]:
    """Read an env var by name (e.g. a webhook named in noktah_brands); None if unset/empty."""
    import os
    value = os.environ.get(name, "").strip()
    return value or None
