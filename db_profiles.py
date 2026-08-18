import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from os import environ
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger("db_manager")

CONFIG_DATA_DIR = Path(__file__).parent / "config_data"
FILENAME = "db_profiles.json"

# Sets of .env variables the file is seeded from on first start.
# After that .env plays no part in the life of the connections.
SEED_ENV_SETS: list[tuple[str, str]] = [
    ("grom-tk", ""),
    ("grom-prod", "_PROD"),
    ("grom-my", "_MY"),
]

MIN_PORT = 1
MAX_PORT = 65535


@dataclass(frozen=True)
class DBProfile:
    id: str
    name: str
    host: str
    port: int
    user: str
    password: str
    dbname: str

    @property
    def url(self) -> str:
        return build_url(self.host, self.port, self.user, self.password, self.dbname)


def build_url(host: str, port: str | int, user: str, password: str, dbname: str) -> str:
    """Connection URL. User and password are percent-encoded: a password typed
    as `p@ss:w/ord` would otherwise be parsed as part of the host."""
    return (
        f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{dbname}"
    )


def _path() -> Path:
    return CONFIG_DATA_DIR / FILENAME


def _validate(name: str, host: str, port: str | int, user: str, dbname: str) -> int:
    """Validate the connection fields and return the normalised port."""
    for label, value in (("Name", name), ("Host", host), ("Username", user), ("Database", dbname)):
        if not str(value).strip():
            raise ValueError(f"Поле «{label}» не может быть пустым")

    try:
        port_number = int(str(port).strip())
    except ValueError:
        raise ValueError("Порт должен быть числом") from None

    if not MIN_PORT <= port_number <= MAX_PORT:
        raise ValueError(f"Порт должен быть в диапазоне {MIN_PORT}–{MAX_PORT}")

    return port_number


def _seed_from_env() -> list[DBProfile]:
    """Profiles from .env. A set with a missing or broken variable is skipped."""
    profiles: list[DBProfile] = []
    for name, suffix in SEED_ENV_SETS:
        values = {
            key: environ.get(f"DB_{key.upper()}{suffix}")
            for key in ("host", "port", "user", "password", "name")
        }
        if any(value is None for value in values.values()):
            continue
        try:
            port = _validate(name, values["host"], values["port"], values["user"], values["name"])
        except ValueError:
            continue
        profiles.append(DBProfile(
            id=str(uuid.uuid4()),
            name=name,
            host=values["host"].strip(),
            port=port,
            user=values["user"].strip(),
            password=values["password"],
            dbname=values["name"].strip(),
        ))
    return profiles


def _write(profiles: list[DBProfile]) -> None:
    """Atomic write with mode 0600.

    Writes to a temporary file alongside and swaps it in with os.replace():
    overwriting in place would leave a truncated file if the process died
    between truncation and write, and nothing could read it afterwards — the
    app would 500 on every page, including the database picker.
    """
    CONFIG_DATA_DIR.mkdir(exist_ok=True)
    payload = json.dumps([asdict(p) for p in profiles], ensure_ascii=False, indent=2)

    tmp_path = _path().with_suffix(".json.tmp")
    # The file holds passwords — create it 0600 outright rather than by umask.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp_path, _path())


def load() -> list[DBProfile]:
    """The list of connections, seeded from .env on first access.

    The file is read every time: there is a single worker (uvicorn without
    --workers), the file is tiny, and having no cache rules out pages
    disagreeing about the list.
    """
    path = _path()
    if not path.exists():
        seeded = _seed_from_env()
        if not seeded:
            # Do not write an empty file: a fixed .env would never seed again.
            return []
        _write(seeded)
        return seeded

    # A corrupt file must not take the app down: with no connections the
    # /auth/db-select screen still works and shows that the list is empty. No
    # seeding from .env here — the file exists, and overwriting it would lose
    # content that can still be repaired by hand.
    try:
        return [DBProfile(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.error("Файл подключений %s нечитаем: %s", path, e)
        return []


def targets_same_database(a: DBProfile, b: DBProfile) -> bool:
    """Whether both records point at the same database.

    Name, user and password do not count: changing a password leaves the user
    in the same database, while changing host/port/database means a different
    one with different users.
    """
    return (a.host, a.port, a.dbname) == (b.host, b.port, b.dbname)


def get(profile_id: str) -> DBProfile | None:
    return next((p for p in load() if p.id == profile_id), None)


def add(name: str, host: str, port: str | int, user: str, password: str, dbname: str) -> DBProfile:
    port_number = _validate(name, host, port, user, dbname)
    profile = DBProfile(
        id=str(uuid.uuid4()),
        name=name.strip(),
        host=host.strip(),
        port=port_number,
        user=user.strip(),
        password=password,
        dbname=dbname.strip(),
    )
    _write([*load(), profile])
    return profile


def update(
    profile_id: str, name: str, host: str, port: str | int, user: str, password: str, dbname: str,
) -> DBProfile:
    port_number = _validate(name, host, port, user, dbname)
    profiles = load()
    if not any(p.id == profile_id for p in profiles):
        raise ValueError("Подключение не найдено")

    updated = DBProfile(
        id=profile_id,
        name=name.strip(),
        host=host.strip(),
        port=port_number,
        user=user.strip(),
        password=password,
        dbname=dbname.strip(),
    )
    _write([updated if p.id == profile_id else p for p in profiles])
    return updated


def delete(profile_id: str, active_id: str) -> None:
    """Delete a connection.

    active_id is passed in rather than read from db_manager: that module
    already depends on this one, so the reverse import would close a cycle.
    """
    profiles = load()
    if not any(p.id == profile_id for p in profiles):
        raise ValueError("Подключение не найдено")
    if profile_id == active_id:
        raise ValueError("Нельзя удалить активное подключение — сначала переключитесь на другое")
    if len(profiles) == 1:
        raise ValueError("Нельзя удалить последнее подключение")

    _write([p for p in profiles if p.id != profile_id])
