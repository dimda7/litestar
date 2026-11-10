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
        chunk = valid_rows[start : start + DELETE_CHUNK]
        ids = ", ".join(str(vr["id"]) for vr in chunk)
        numbers = ", ".join(f"'{sql_escape(vr['number_ptoir'])}'" for vr in chunk)
        sql_lines += [
            f"UPDATE public.orders SET id_ptoir = NULL WHERE id_ptoir IN ({ids});",
            f"DELETE FROM public.ptoir_level_warning WHERE id_ptoir IN ({ids});",
            f"DELETE FROM public.ptoir WHERE id IN ({ids});",
            f"DELETE FROM public.ptoir_status WHERE number_ptoir IN ({numbers});",
        ]
    return sql_lines


MAIN_PTOIR_LINKS = ("actives_to_main_ptoir", "design_number_to_main_ptoir", "main_ptoir_trigger")

# (link table, its column, the table it links to): rows reached only through a deleted
# БПТОиР's link would be orphaned, so they go with it.
MAIN_PTOIR_OWNED = (
    ("main_ptoir_to_config_counter", "id_main_ptoir_config", "main_ptoir_config"),
    ("main_ptoir_to_techcard_interval", "id_techcard_interval", "techcard_interval"),
)


def delete_main(valid_rows: list[dict]) -> list[str]:
    # The ПТОиР go first through delete() itself, so both operations stay identical;
    # main_ptoir points at main_ptoir_status (the history by number, without a FK back).
    sql_lines = delete([ptoir for vr in valid_rows for ptoir in vr["ptoirs"]])
    for start in range(0, len(valid_rows), DELETE_CHUNK):
        chunk = valid_rows[start : start + DELETE_CHUNK]
        ids = ", ".join(str(vr["id"]) for vr in chunk)
        numbers = ", ".join(f"'{sql_escape(vr['number_ptoir'])}'" for vr in chunk)
        sql_lines += [f"DELETE FROM public.{table} WHERE id_main_ptoir IN ({ids});" for table in MAIN_PTOIR_LINKS]
        # One statement per link table: the CTE removes the links, the outer DELETE the rows
        # they pointed at. Both see the same snapshot, so a link of another БПТОиР is told
        # apart by its id_main_ptoir; the FK is checked at the end of the statement.
        sql_lines += [
            f"WITH links AS (DELETE FROM public.{link} WHERE id_main_ptoir IN ({ids}) RETURNING {column}) "
            f"DELETE FROM public.{owned} o USING links WHERE o.id = links.{column} AND NOT EXISTS "
            f"(SELECT 1 FROM public.{link} l WHERE l.{column} = o.id AND l.id_main_ptoir NOT IN ({ids}));"
            for link, column, owned in MAIN_PTOIR_OWNED
        ]
        sql_lines += [
            f"DELETE FROM public.main_ptoir WHERE id IN ({ids});",
            f"DELETE FROM public.main_ptoir_status WHERE number_ptoir IN ({numbers});",
        ]
    return sql_lines
