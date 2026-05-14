"""Centralised configuration via pydantic-settings (reads .env automatically)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-opus-4-7"
    agent_max_steps: int = 100

    # VNC browser sandbox
    vnc_browser_url: str = "http://vnc-browser:8080"
    vnc_container_name: str = "vnc_browser"

    # vtrack FastAPI
    vtrack_api_url: str = "http://api:8000"

    # Login credentials (loaded from secrets at runtime)
    login_email: str
    login_password: str

    # Scheduler (Mon–Fri, America/Bogota timezone)
    schedule_morning: str = "0 6 * * 1-5"    # 6:00 AM
    schedule_afternoon: str = "0 15 * * 1-5"  # 3:00 PM
    timezone: str = "America/Bogota"

    # Action script recording / replay
    action_script_path: str = "/data/action_script.json"
    replay_randomness_pct: float = 0.20
    max_inter_step_ms: float = 3000.0

    # Service
    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
