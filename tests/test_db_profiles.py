import json

import pytest

import db_profiles

ENV_VARS = [
    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "DB_HOST_PROD", "DB_PORT_PROD", "DB_USER_PROD", "DB_PASSWORD_PROD", "DB_NAME_PROD",
    "DB_HOST_MY", "DB_PORT_MY", "DB_USER_MY", "DB_PASSWORD_MY", "DB_NAME_MY",
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated storage directory and an empty environment instead of real .env."""
    monkeypatch.setattr(db_profiles, "CONFIG_DATA_DIR", tmp_path)
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def set_env(monkeypatch, suffix: str, host: str = "localhost", port: str = "5432") -> None:
    monkeypatch.setenv(f"DB_HOST{suffix}", host)
    monkeypatch.setenv(f"DB_PORT{suffix}", port)
    monkeypatch.setenv(f"DB_USER{suffix}", "postgres")
    monkeypatch.setenv(f"DB_PASSWORD{suffix}", "secret")
    monkeypatch.setenv(f"DB_NAME{suffix}", "grom")


def add_profile(name: str = "test", host: str = "localhost", dbname: str = "grom"):
    return db_profiles.add(
        name=name, host=host, port="5432", user="postgres", password="secret", dbname=dbname,
    )


def test_seed_creates_profile_per_env_set(store, monkeypatch):
    set_env(monkeypatch, "", host="tk-host")
    set_env(monkeypatch, "_PROD", host="prod-host")
    set_env(monkeypatch, "_MY", host="my-host")

    profiles = db_profiles.load()

    assert [p.name for p in profiles] == ["grom-tk", "grom-prod", "grom-my"]
    assert [p.host for p in profiles] == ["tk-host", "prod-host", "my-host"]


def test_seed_gives_each_profile_its_own_id(store, monkeypatch):
    set_env(monkeypatch, "")
    set_env(monkeypatch, "_PROD")

    ids = [p.id for p in db_profiles.load()]

    assert len(set(ids)) == 2
    assert all(ids)


def test_seed_skips_incomplete_env_set(store, monkeypatch):
    set_env(monkeypatch, "")
    monkeypatch.setenv("DB_HOST_PROD", "prod-host")  # the other variables of the set are missing

    profiles = db_profiles.load()

    assert [p.name for p in profiles] == ["grom-tk"]


def test_seed_skips_env_set_with_non_numeric_port(store, monkeypatch):
    set_env(monkeypatch, "")
    set_env(monkeypatch, "_PROD", port="not-a-port")

    assert [p.name for p in db_profiles.load()] == ["grom-tk"]


def test_seed_runs_once_and_env_is_ignored_afterwards(store, monkeypatch):
    set_env(monkeypatch, "", host="tk-host")
    db_profiles.load()

    monkeypatch.setenv("DB_HOST", "changed-host")
    set_env(monkeypatch, "_PROD")

    profiles = db_profiles.load()

    assert [p.name for p in profiles] == ["grom-tk"]
    assert profiles[0].host == "tk-host"


def test_seed_writes_no_file_when_env_is_empty(store):
    assert db_profiles.load() == []
    assert not (store / db_profiles.FILENAME).exists()


def test_add_appends_profile(store, monkeypatch):
    set_env(monkeypatch, "")
    db_profiles.load()

    created = add_profile(name="Стенд Иванова", host="10.0.0.5")

    profiles = db_profiles.load()
    assert [p.name for p in profiles] == ["grom-tk", "Стенд Иванова"]
    assert db_profiles.get(created.id).host == "10.0.0.5"


def test_add_generates_unique_ids(store):
    first = add_profile(name="a")
    second = add_profile(name="b")

    assert first.id != second.id


def test_add_allows_duplicate_names(store):
    add_profile(name="одинаково")
    add_profile(name="одинаково")

    assert len(db_profiles.load()) == 2


def test_add_allows_empty_password(store):
    created = db_profiles.add(
        name="trust", host="localhost", port="5432", user="postgres", password="", dbname="grom",
    )

    assert db_profiles.get(created.id).password == ""


def test_add_strips_surrounding_whitespace(store):
    created = db_profiles.add(
        name="  имя  ", host=" localhost ", port=" 5432 ", user=" postgres ",
        password="secret", dbname=" grom ",
    )

    assert (created.name, created.host, created.user, created.dbname) == (
        "имя", "localhost", "postgres", "grom",
    )


@pytest.mark.parametrize("field", ["name", "host", "user", "dbname"])
def test_add_rejects_empty_required_field(store, field):
    kwargs = {
        "name": "test", "host": "localhost", "port": "5432",
        "user": "postgres", "password": "secret", "dbname": "grom",
    }
    kwargs[field] = "   "

    with pytest.raises(ValueError):
        db_profiles.add(**kwargs)

    assert db_profiles.load() == []


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", ""])
def test_add_rejects_invalid_port(store, port):
    with pytest.raises(ValueError):
        db_profiles.add(
            name="test", host="localhost", port=port,
            user="postgres", password="secret", dbname="grom",
        )

    assert db_profiles.load() == []


def test_update_changes_fields_and_keeps_id(store):
    created = add_profile(name="было", host="old-host")

    updated = db_profiles.update(
        created.id, name="стало", host="new-host", port="5433",
        user="app", password="new-secret", dbname="other",
    )

    assert updated.id == created.id
    stored = db_profiles.get(created.id)
    assert (stored.name, stored.host, stored.port, stored.dbname) == (
        "стало", "new-host", 5433, "other",
    )


def test_update_leaves_other_profiles_untouched(store):
    first = add_profile(name="первый", host="host-1")
    second = add_profile(name="второй", host="host-2")

    db_profiles.update(
        second.id, name="второй*", host="host-2*", port="5432",
        user="postgres", password="secret", dbname="grom",
    )

    assert db_profiles.get(first.id).host == "host-1"


def test_update_rejects_unknown_id(store):
    add_profile()

    with pytest.raises(ValueError):
        db_profiles.update(
            "no-such-id", name="x", host="h", port="5432",
            user="u", password="p", dbname="d",
        )


def test_update_rejects_invalid_port(store):
    created = add_profile(host="old-host")

    with pytest.raises(ValueError):
        db_profiles.update(
            created.id, name="test", host="new-host", port="99999",
            user="postgres", password="secret", dbname="grom",
        )

    assert db_profiles.get(created.id).host == "old-host"


def test_delete_removes_only_the_requested_profile(store):
    first = add_profile(name="первый")
    second = add_profile(name="второй")

    db_profiles.delete(second.id, active_id=first.id)

    assert [p.id for p in db_profiles.load()] == [first.id]


def test_delete_rejects_active_profile(store):
    first = add_profile(name="первый")
    add_profile(name="второй")

    with pytest.raises(ValueError):
        db_profiles.delete(first.id, active_id=first.id)

    assert len(db_profiles.load()) == 2


def test_delete_rejects_last_remaining_profile(store):
    only = add_profile(name="единственный")

    with pytest.raises(ValueError):
        db_profiles.delete(only.id, active_id="some-other-id")

    assert len(db_profiles.load()) == 1


def test_delete_rejects_unknown_id(store):
    first = add_profile(name="первый")
    add_profile(name="второй")

    with pytest.raises(ValueError):
        db_profiles.delete("no-such-id", active_id=first.id)

    assert len(db_profiles.load()) == 2


def test_get_returns_none_for_unknown_id(store):
    add_profile()

    assert db_profiles.get("no-such-id") is None


def test_url_is_built_from_stored_fields(store):
    created = db_profiles.add(
        name="test", host="10.0.0.5", port="6432",
        user="app", password="pwd", dbname="grom",
    )

    assert created.url == "postgresql+asyncpg://app:pwd@10.0.0.5:6432/grom"


def test_url_escapes_special_characters_in_credentials(store):
    created = db_profiles.add(
        name="test", host="10.0.0.5", port="5432",
        user="do/main", password="p@ss:w/ord", dbname="grom",
    )

    from sqlalchemy.engine import make_url

    parsed = make_url(created.url)
    assert parsed.username == "do/main"
    assert parsed.password == "p@ss:w/ord"
    assert parsed.host == "10.0.0.5"
    assert parsed.database == "grom"


def test_file_is_not_readable_by_other_users(store):
    add_profile()

    mode = (store / db_profiles.FILENAME).stat().st_mode & 0o777

    assert mode == 0o600


def test_broken_file_degrades_to_empty_list(store):
    add_profile()
    (store / db_profiles.FILENAME).write_text("{ это не json", encoding="utf-8")

    assert db_profiles.load() == []


def test_file_with_unexpected_shape_degrades_to_empty_list(store):
    add_profile()
    (store / db_profiles.FILENAME).write_text('[{"host": "no-other-fields"}]', encoding="utf-8")

    assert db_profiles.load() == []


def test_broken_file_is_not_reseeded_from_env(store, monkeypatch):
    add_profile()
    (store / db_profiles.FILENAME).write_text("{ это не json", encoding="utf-8")
    set_env(monkeypatch, "")

    assert db_profiles.load() == []


def test_targets_same_database_ignores_name_and_credentials(store):
    created = add_profile(name="было", host="host")
    same = db_profiles.DBProfile(
        id=created.id, name="стало", host="host", port=created.port,
        user="other-user", password="other-password", dbname=created.dbname,
    )

    assert db_profiles.targets_same_database(created, same)


@pytest.mark.parametrize("field,value", [("host", "other"), ("port", 5433), ("dbname", "other")])
def test_targets_same_database_detects_new_target(store, field, value):
    created = add_profile()
    moved = db_profiles.DBProfile(**{**vars(created), field: value})

    assert not db_profiles.targets_same_database(created, moved)


def test_stored_file_is_readable_json(store):
    created = add_profile(name="читаемо")

    raw = json.loads((store / db_profiles.FILENAME).read_text(encoding="utf-8"))

    assert raw == [{
        "id": created.id, "name": "читаемо", "host": "localhost", "port": 5432,
        "user": "postgres", "password": "secret", "dbname": "grom",
    }]
