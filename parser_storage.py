import json
import time
from pathlib import Path

PARSER_DATA_DIR = Path(__file__).parent / "parser_data"
PARSER_DATA_DIR.mkdir(exist_ok=True)

LOG_DIR = Path(__file__).parent / "log"
LOG_DIR.mkdir(exist_ok=True)

CLEANUP_AGE_SECONDS = 3600


def load_data(session_id: str) -> dict | None:
    path = PARSER_DATA_DIR / f"{session_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_data(session_id: str, data: dict) -> None:
    path = PARSER_DATA_DIR / f"{session_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def cleanup_old_files() -> None:
    now = time.time()
    for f in PARSER_DATA_DIR.glob("*.json"):
        if now - f.stat().st_mtime > CLEANUP_AGE_SECONDS:
            f.unlink(missing_ok=True)
