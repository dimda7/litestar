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


HOURS_COUNTER_TYPE_ID = 1


def change_hours(valid_rows: list[dict]) -> list[str]:
    # replica turns counter_active_hours_jump off: with it on, the written value
    # is taken as a source reading and summed into the old one instead of replacing it.
    return [
        "SET session_replication_role = replica;",
        *(
            f"UPDATE public.counter_active SET value = {vr['value']} "
            f"WHERE id_active = {vr['id_active']} AND id_counter_type = {HOURS_COUNTER_TYPE_ID};"
            for vr in valid_rows
        ),
        "SET session_replication_role = DEFAULT;",
    ]


DELETE_CHUNK = 1000


def delete(valid_rows: list[dict]) -> list[str]:
    # Order is set by the FKs: orders and ptoir_level_warning point at ptoir,
    # ptoir points at ptoir_status (which keeps the whole history by number, without a FK).
    sql_lines: list[str] = []
    for start in range(0, len(valid_rows), DELETE_CHUNK):
        chunk = valid_rows[start:start + DELETE_CHUNK]
        ids = ", ".join(str(vr["id"]) for vr in chunk)
        numbers = ", ".join(f"'{sql_escape(vr['number_ptoir'])}'" for vr in chunk)
        sql_lines += [
            f"UPDATE public.orders SET id_ptoir = NULL WHERE id_ptoir IN ({ids});",
            f"DELETE FROM public.ptoir_level_warning WHERE id_ptoir IN ({ids});",
            f"DELETE FROM public.ptoir WHERE id IN ({ids});",
            f"DELETE FROM public.ptoir_status WHERE number_ptoir IN ({numbers});",
        ]
    return sql_lines
