import logging

logger = logging.getLogger("ptoir_parser")

PREFIX = "ptoir_parser"
PATH = "/ptoir-parser"


def find_column(rows: list[dict], name: str) -> str | None:
    return next((k for k in (rows[0] if rows else {}) if str(k).strip().lower() == name), None)
