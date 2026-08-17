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

# Наборы переменных .env, из которых засевается файл при первом запуске.
# Дальше .env в жизни подключений не участвует.
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
    """URL подключения. Логин и пароль экранируются: набранный в форме пароль
    вида `p@ss:w/ord` иначе разбирается как часть хоста."""
    return (
        f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{dbname}"
    )


def _path() -> Path:
    return CONFIG_DATA_DIR / FILENAME


def _validate(name: str, host: str, port: str | int, user: str, dbname: str) -> int:
    """Проверяет поля подключения и возвращает нормализованный порт."""
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
    """Профили из .env. Набор с отсутствующей/битой переменной пропускается."""
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
    """Атомарная запись с правами 0600.

    Пишем во временный файл рядом и подменяем через os.replace(): прямая
    перезапись оставила бы обрезанный файл, если процесс умрёт между
    усечением и записью, а читать его потом некому — приложение отдавало бы
    500 на каждой странице, включая экран выбора БД.
    """
    CONFIG_DATA_DIR.mkdir(exist_ok=True)
    payload = json.dumps([asdict(p) for p in profiles], ensure_ascii=False, indent=2)

    tmp_path = _path().with_suffix(".json.tmp")
    # В файле лежат пароли — создаём сразу с 0600, а не по umask.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp_path, _path())


def load() -> list[DBProfile]:
    """Список подключений. При первом обращении засевается из .env.

    Файл читается каждый раз: воркер один (uvicorn без --workers), файл
    крошечный, а отсутствие кэша снимает рассинхрон между страницами.
    """
    path = _path()
    if not path.exists():
        seeded = _seed_from_env()
        if not seeded:
            # Не пишем пустой файл: иначе поправленный .env уже не засеется.
            return []
        _write(seeded)
        return seeded

    # Битый файл не должен ронять приложение: без подключений остаётся
    # рабочим экран /auth/db-select, где видно, что список пуст. Засев из
    # .env здесь не запускаем — файл существует, и перезаписать его значило
    # бы потерять содержимое, которое ещё можно починить руками.
    try:
        return [DBProfile(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.error("Файл подключений %s нечитаем: %s", path, e)
        return []


def targets_same_database(a: DBProfile, b: DBProfile) -> bool:
    """Ведут ли записи в одну и ту же БД.

    Имя, логин и пароль не в счёт: смена пароля оставляет пользователя в той
    же базе, а смена host/port/database — уже другая база с другими
    пользователями.
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
    """Удаляет подключение.

    active_id передаётся параметром, а не читается из db_manager: тот сам
    зависит от этого модуля, и обратный импорт замкнул бы цикл.
    """
    profiles = load()
    if not any(p.id == profile_id for p in profiles):
        raise ValueError("Подключение не найдено")
    if profile_id == active_id:
        raise ValueError("Нельзя удалить активное подключение — сначала переключитесь на другое")
    if len(profiles) == 1:
        raise ValueError("Нельзя удалить последнее подключение")

    _write([p for p in profiles if p.id != profile_id])
