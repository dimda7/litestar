from dataclasses import dataclass, field
from os import environ

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class DBProfile:
    label: str
    host: str
    port: int
    user: str
    password: str
    dbname: str

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.dbname}"
        )


DEFAULT_DB_PROFILE = "grom-tk"


@dataclass(frozen=True)
class Settings:
    db_profiles: dict[str, DBProfile] = field(default_factory=lambda: {
        "grom-tk": DBProfile(
            label="grom-tk",
            host=environ["DB_HOST"],
            port=int(environ["DB_PORT"]),
            user=environ["DB_USER"],
            password=environ["DB_PASSWORD"],
            dbname=environ["DB_NAME"],
        ),
        "grom-prod": DBProfile(
            label="grom-prod",
            host=environ["DB_HOST_PROD"],
            port=int(environ["DB_PORT_PROD"]),
            user=environ["DB_USER_PROD"],
            password=environ["DB_PASSWORD_PROD"],
            dbname=environ["DB_NAME_PROD"],
        ),
    })
    server_port: int = field(default_factory=lambda: int(environ.get("SERVER_PORT", "8011")))
    session_secret: bytes = field(default_factory=lambda: bytes.fromhex(environ["SESSION_SECRET"]))
    log_level: str = field(default_factory=lambda: environ.get("LOG_LEVEL", "INFO"))


settings = Settings()
