from datetime import date, datetime

from sql_utils import sql_escape


def insert_train(
    id_train: int, id_type_train: int, train_name: str, valid_rows: list[dict], id_train_series: int,
    count_car: int | None = None,
) -> list[str]:
    """Build the body of the train insert script (without BEGIN/COMMIT).

    Shared by "Скачать SQL-файл" (wrapped in BEGIN;...COMMIT; and served as
    a file) and "Выполнить в базе данных" (run as a single multi-statement
    query inside the already open session) — both paths build the same SQL
    so their behaviour cannot drift apart.
    """
    now = datetime.utcnow().replace(microsecond=0)
    today = date.today()
    sql_lines: list[str] = []

    sql_lines.append(f"INSERT INTO public.train (id, id_train_type, name, is_active, is_delete) VALUES ({id_train}, {id_type_train}, '{sql_escape(train_name)}', true, false);")
    sql_lines.append(f"INSERT INTO public.mileage_train (id_train, milage, mileage_average, date, date_average) VALUES ({id_train}, 0, 0, '{now}', '{today}');")

    # id_location/id_actives are not precomputed as max(id)+1 — that snapshot
    # could go stale by execution time and collide on the PK. The ids come
    # from the sequence inside the script instead. One nextval() per variable
    # blew DECLARE up to thousands of lines (id1..idN), so a single query
    # collects an array of ids for all rows at once, indexed into as
    # loc_ids[i].
    body_lines: list[str] = []

    for idx, vr in enumerate(valid_rows, start=1):
        loc_ref = f"loc_ids[{idx}]"
        act_ref = f"act_ids[{idx}]"

        sn = vr["serial_number"]
        sn_val = f"'{sql_escape(sn)}'" if sn else "NULL"
        parent_val = f"'{sql_escape(str(vr['id_actives_parent']))}'" if vr["id_actives_parent"] else "NULL"
        root_val = f"'{sql_escape(str(vr['root_number']))}'" if vr["root_number"] else "NULL"
        car_num_val = str(vr["car_number"]) if vr["car_number"] is not None else "NULL"
        cp_val = str(vr["car_place_id"]) if vr["car_place_id"] is not None else "NULL"
        ut_val = str(vr["id_unit_type"]) if vr["id_unit_type"] is not None else "NULL"

        body_lines.append(
            f"    INSERT INTO public.location (id, id_type_location, id_train, car_number, id_car_place) "
            f"VALUES ({loc_ref}, 2, {id_train}, {car_num_val}, {cp_val});"
        )
        body_lines.append(
            f"    INSERT INTO public.actives (id, active_number, id_unit_type, id_design_number, id_location, "
            f"serial_number, lcn, id_actves_parent, id_actives_root) "
            f"VALUES ({act_ref}, '{sql_escape(vr['active_number'])}', {ut_val}, {vr['id_design_number']}, "
            f"{loc_ref}, {sn_val}, '{sql_escape(vr['lcn_new'])}', {parent_val}, {root_val});"
        )

        if vr["is_root"]:
            body_lines.append(f"    UPDATE public.counter_active SET is_train = true WHERE id_active = {act_ref};")

    sql_lines.append("DO $$")
    sql_lines.append("DECLARE")
    sql_lines.append(
        f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
        f"FROM generate_series(1, {len(valid_rows)}));"
    )
    sql_lines.append(
        f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
        f"FROM generate_series(1, {len(valid_rows)}));"
    )
    sql_lines.append("BEGIN")
    sql_lines.extend(body_lines)
    sql_lines.append("END $$;")

    sql_lines.append(
        f"UPDATE public.train AS t SET active = act.id "
        f"FROM public.location AS loc LEFT JOIN public.actives act ON act.id_location = loc.id "
        f"WHERE nlevel(act.lcn) = 1 AND loc.id_train = t.id AND t.id = {id_train};"
    )

    sql_lines.append(
        f"UPDATE public.train SET id_train_series = {id_train_series} WHERE id = {id_train};"
    )

    if count_car is not None:
        sql_lines.append(
            f"UPDATE public.train SET count_car = {count_car} WHERE id = {id_train};"
        )

    sql_lines.append("SELECT nextval('public.location_id_seq');")
    sql_lines.append("SELECT nextval('public.actives_id_seq');")

    return sql_lines
