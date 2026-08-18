from datetime import datetime

from sql_utils import sql_escape


def update_ptoir(valid_rows: list[tuple[int, datetime, int, int, int]]) -> list[str]:
    sql_lines: list[str] = []
    for ptoir_id, date_activation, interval, level_warning_id, zero_point_value in valid_rows:
        date_str = date_activation.strftime("%Y-%m-%d %H:%M:%S")
        sql_lines.append(
            f"UPDATE public.ptoir SET date_activation = '{sql_escape(date_str)}', "
            f"interval = {interval}, is_active = TRUE WHERE id = {ptoir_id};"
        )
        sql_lines.append(
            f"UPDATE public.ptoir_level_warning SET zero_point_value = {zero_point_value} "
            f"WHERE id = {level_warning_id};"
        )
    return sql_lines
