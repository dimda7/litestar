from dataclasses import dataclass, field
from os import environ

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class JiraSettings:
    host: str
    port: int
    user: str
    password: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    # DB connections live in config_data/db_profiles.json (see db_profiles.py).
    # The DB_* variables only seed that file on first start.
    jira: JiraSettings = field(default_factory=lambda: JiraSettings(
        host=environ["JIIRA_HOST"],
        port=int(environ["JIIRA_PORT"]),
        user=environ["JIIRA_USER"],
        password=environ["JIIRA_PASSWORD"],
    ))
    server_port: int = field(default_factory=lambda: int(environ.get("SERVER_PORT", "8011")))
    session_secret: bytes = field(default_factory=lambda: bytes.fromhex(environ["SESSION_SECRET"]))
    log_level: str = field(default_factory=lambda: environ.get("LOG_LEVEL", "INFO"))


settings = Settings()
