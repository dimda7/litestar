def sql_escape(value: str) -> str:
    """Экранирует одинарные кавычки для подстановки строки в SQL-литерал."""
    return value.replace("'", "''")
