"""Non-credential configuration. Credentials never live here -- see SOP SS3."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Unknown env vars are ignored (SOP SS1.2)."""

    model_config = SettingsConfigDict(extra="ignore")

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080
    do_api_base_url: str = "https://api.digitalocean.com"


settings = Settings()
