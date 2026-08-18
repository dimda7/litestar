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


def _jira_from_env() -> JiraSettings | None:
    """Jira settings, or None when the JIIRA_* block is not filled in.

    Reading them with environ[...] used to kill the whole app at import time on
    a deployment that simply does not use Jira — and .env.example ships the keys
    empty, so following the README literally was enough to hit it. An incomplete
    or non-numeric block is skipped the same way db_profiles skips a broken DB_*
    set; jira_client then reports the absence per request.
    """
    values = {key: environ.get(f"JIIRA_{key.upper()}") for key in ("host", "port", "user", "password")}
    if not all(values.values()):
        return None

    try:
        port = int(values["port"])
    except ValueError:
        return None

    return JiraSettings(host=values["host"], port=port, user=values["user"], password=values["password"])


@dataclass(frozen=True)
class Settings:
    # DB connections live in config_data/db_profiles.json (see db_profiles.py).
    # The DB_* variables only seed that file on first start.
    jira: JiraSettings | None = field(default_factory=_jira_from_env)
    server_port: int = field(default_factory=lambda: int(environ.get("SERVER_PORT", "8011")))
    session_secret: bytes = field(default_factory=lambda: bytes.fromhex(environ["SESSION_SECRET"]))
    log_level: str = field(default_factory=lambda: environ.get("LOG_LEVEL", "INFO"))


settings = Settings()
