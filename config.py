from dataclasses import dataclass, field
from os import environ

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    db_host: str = field(default_factory=lambda: environ["DB_HOST"])
    db_port: int = field(default_factory=lambda: int(environ["DB_PORT"]))
    db_user: str = field(default_factory=lambda: environ["DB_USER"])
    db_password: str = field(default_factory=lambda: environ["DB_PASSWORD"])
    db_name: str = field(default_factory=lambda: environ["DB_NAME"])
    server_port: int = field(default_factory=lambda: int(environ.get("SERVER_PORT", "8011")))

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
