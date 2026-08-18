def sql_escape(value: str) -> str:
    """Escape single quotes so the string can be inlined into an SQL literal."""
    return value.replace("'", "''")
