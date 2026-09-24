"""Configuration, from environment variables only (docker-compose passes them)."""
from functools import lru_cache

from pydantic import Field
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
    access_hub_aud: str ="0aae0ce6863685fab39723bffab5cb0580dbd13f491e3c2807817255b9d6bb02"

    @property
    def access_certs_url(self) -> str:
        return f"https://{self.access_team_domain}/cdn-cgi/access/certs"

    @property
    def access_issuer(self) -> str:
        return f"https://{self.access_team_domain}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
