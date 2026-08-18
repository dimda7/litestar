from sql_utils import sql_escape

ACTIVE_NUMBER_COUNTER_DESCRIPTION = "Номер следующего актива"
# active_number must be exactly this long: a letter prefix (asset type) plus the
# counter digits, zero-padded on the left between prefix and number to that length.
ACTIVE_NUMBER_LENGTH = 10
# counter_type of the mileage counter ('Пробег') in counter_active
MILEAGE_COUNTER_TYPE_ID = 3


def update_design_number(valid_rows: list[tuple[str, int, str]], with_comment: bool = False) -> list[str]:
    sql_lines: list[str] = []
    for active_number, design_number_id, design_number in valid_rows:
        comment = f" -- '{sql_escape(design_number)}'" if with_comment else ""
        sql_lines.append(
            f"UPDATE public.actives SET id_design_number = {design_number_id} "
            f"WHERE active_number = '{sql_escape(active_number)}';{comment}"
        )
    return sql_lines


def update_serial_number(valid_rows: list[tuple[str, str]]) -> list[str]:
    return [
        f"UPDATE public.actives SET serial_number = '{sql_escape(serial_number)}' "
        f"WHERE active_number = '{sql_escape(active_number)}';"
        for active_number, serial_number in valid_rows
    ]


def recount_mileage(valid_rows: list[dict]) -> list[str]:
    """Build the mileage recount SQL (without BEGIN/COMMIT), shared by download and execution.

    The order is mandatory: first the milage_const from the file (UPDATE, or INSERT
    for assets without mileage_start), then UPDATE mileage_start.milage — which
    reads milage_const at execution time and must already see the new value — then
    UPDATE counter_active, whose recount through function_get_mileage() reads
    mileage_start.milage. All in one transaction, so a file downloaded and run later
    cannot drift from the database. For trains (id_unit_type=1) the counter is not
    recomputed (as in the old script).
    """
    sql_lines: list[str] = []
    for vr in valid_rows:
        if vr["milage_const"] is None:
            continue
        if vr["insert_mileage_start"]:
            sql_lines.append(
                f"INSERT INTO public.mileage_start (id_active, milage, milage_const, is_recount) "
                f"VALUES ({vr['id_active']}, {vr['milage_const']}, {vr['milage_const']}, true); "
                f"-- '{sql_escape(vr['active_number'])}'"
            )
        else:
            sql_lines.append(
                f"UPDATE public.mileage_start SET milage_const = {vr['milage_const']}, is_recount = true "
                f"WHERE id_active = {vr['id_active']}; -- '{sql_escape(vr['active_number'])}'"
            )
    for vr in valid_rows:
        sql_lines.append(
            f"UPDATE public.mileage_start SET milage = COALESCE(milage_const, 0) + ({vr['total']}), "
            f"is_recount = true WHERE id_active = {vr['id_active']}; -- '{sql_escape(vr['active_number'])}'"
        )
    for vr in valid_rows:
        if vr["is_train"]:
            continue
        sql_lines.append(
            f"UPDATE public.counter_active SET value = COALESCE("
            f"(SELECT sum FROM public.function_get_mileage(id_active, date::date)), 0) "
            f"WHERE id_active = {vr['id_active']} AND id_counter_type = {MILEAGE_COUNTER_TYPE_ID}; "
            f"-- '{sql_escape(vr['active_number'])}'"
        )
    return sql_lines


def create_actives(valid_rows: list[dict]) -> list[str]:
    """Build the body of the script creating assets from materials (without BEGIN/COMMIT).

    Every asset gets its own location record (materials is not used). Shared by
    "Скачать SQL-файл" and "Выполнить в базе данных". All counters are read and
    consumed inside the DO block itself as it runs, not on the Python side during
    generation:
    - ids for location/actives come from nextval() (as in train_parser);
    - storage.last_lcn and iterator_number_last.number come from
      `SELECT ... INTO var ... FOR UPDATE`, are incremented locally, and the same
      variable is written back with `UPDATE` at the end of that block (FOR UPDATE
      holds the row lock until the transaction ends, which rules out a race on
      concurrent runs).
    Without this a file downloaded but executed later (or two runs in a row) could
    drift from the value in the database: an id from nextval() is always unique on its
    own, while last_lcn/number are ordinary integer columns, and the old peewee code
    computed them once on the Python side.
    """
    total_actives = len(valid_rows)
    storage_ids = sorted({vr["id_storage"] for vr in valid_rows})

    sql_lines: list[str] = ["DO $$", "DECLARE"]
    sql_lines.append(
        f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
        f"FROM generate_series(1, {total_actives}));"
    )
    sql_lines.append(
        f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
        f"FROM generate_series(1, {total_actives}));"
    )
    sql_lines.append("    active_num bigint;")
    for storage_id in storage_ids:
        sql_lines.append(f"    lcn_{storage_id} bigint;")

    sql_lines.append("BEGIN")
    sql_lines.append(
        f"    SELECT number INTO active_num FROM public.iterator_number_last "
        f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}' FOR UPDATE;"
    )
    sql_lines.append(
        f"    IF active_num IS NULL THEN RAISE EXCEPTION "
        f"'Счётчик ''{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}'' не найден или пуст'; END IF;"
    )
    for storage_id in storage_ids:
        sql_lines.append(
            f"    SELECT last_lcn INTO lcn_{storage_id} FROM public.storage WHERE id = {storage_id} FOR UPDATE;"
        )

    body_lines: list[str] = []

    for i, vr in enumerate(valid_rows, start=1):
        loc_ref = f"loc_ids[{i}]"
        act_ref = f"act_ids[{i}]"
        sp_val = str(vr["id_storage_place"]) if vr["id_storage_place"] is not None else "NULL"

        # The increments do not touch the location row, but are moved ahead of both
        # INSERTs so location and actives always sit next to each other as one pair.
        lcn_var = f"lcn_{vr['id_storage']}"
        body_lines.append(f"    {lcn_var} := {lcn_var} + 1;")
        body_lines.append("    active_num := active_num + 1;")

        body_lines.append(
            f"    INSERT INTO public.location (id, id_type_location, id_storage, id_storage_place, id_consignment) "
            f"VALUES ({loc_ref}, 1, {vr['id_storage']}, {sp_val}, {vr['id_consignment']});"
        )

        sn_val = f"'{sql_escape(vr['serial_number'])}'" if vr["serial_number"] else "NULL"
        sa_val = f"'{sql_escape(vr['special_account'])}'" if vr["special_account"] else "NULL"
        # An asset number of fixed length ACTIVE_NUMBER_LENGTH: the letter prefix plus
        # the counter digits, zero-padded on the left to the required width (validation
        # above guarantees len(type_active) < ACTIVE_NUMBER_LENGTH, so the width > 0).
        number_width = ACTIVE_NUMBER_LENGTH - len(vr["type_active"])
        active_number_expr = (
            f"'{sql_escape(vr['type_active'])}' || lpad(active_num::text, {number_width}, '0')"
        )
        lcn_expr = f"('S{vr['id_storage']}.' || {lcn_var})::ltree"

        body_lines.append(
            f"    INSERT INTO public.actives (id, active_number, id_design_number, id_location, "
            f"serial_number, lcn, special_account) "
            f"VALUES ({act_ref}, {active_number_expr}, {vr['id_design_number']}, {loc_ref}, "
            f"{sn_val}, {lcn_expr}, {sa_val});"
        )

    sql_lines.extend(body_lines)

    for storage_id in storage_ids:
        sql_lines.append(f"    UPDATE public.storage SET last_lcn = lcn_{storage_id} WHERE id = {storage_id};")
    sql_lines.append(
        f"    UPDATE public.iterator_number_last SET number = active_num "
        f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}';"
    )

    sql_lines.append("END $$;")

    return sql_lines


def create_active_from_model(valid_rows: list[dict]) -> list[str]:
    """Build the SQL creating assets from model positions (without BEGIN/COMMIT).

    Unlike create_actives there is no storage counter here
    (storage.last_lcn under FOR UPDATE) — each asset's real lcn is already determined
    by the model (id_train plus the path from lsn), so FOR UPDATE is only needed on the
    shared asset number counter iterator_number_last.
    """
    total = len(valid_rows)

    sql_lines: list[str] = ["DO $$", "DECLARE"]
    sql_lines.append(
        f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
        f"FROM generate_series(1, {total}));"
    )
    sql_lines.append(
        f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
        f"FROM generate_series(1, {total}));"
    )
    sql_lines.append("    active_num bigint;")
    sql_lines.append("BEGIN")
    sql_lines.append(
        f"    SELECT number INTO active_num FROM public.iterator_number_last "
        f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}' FOR UPDATE;"
    )
    sql_lines.append(
        f"    IF active_num IS NULL THEN RAISE EXCEPTION "
        f"'Счётчик ''{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}'' не найден или пуст'; END IF;"
    )

    for i, vr in enumerate(valid_rows, start=1):
        loc_ref = f"loc_ids[{i}]"
        act_ref = f"act_ids[{i}]"
        sql_lines.append("    active_num := active_num + 1;")

        car_number_val = str(vr["car_number"]) if vr["car_number"] is not None else "NULL"
        car_place_val = str(vr["id_car_place"]) if vr["id_car_place"] is not None else "NULL"
        sql_lines.append(
            f"    INSERT INTO public.location (id, id_type_location, id_train, car_number, id_car_place) "
            f"VALUES ({loc_ref}, 2, {vr['id_train']}, {car_number_val}, {car_place_val});"
        )

        sn_val = f"'{sql_escape(vr['serial_number'])}'" if vr["serial_number"] else "NULL"
        number_width = ACTIVE_NUMBER_LENGTH - len(vr["type_active"])
        active_number_expr = (
            f"'{sql_escape(vr['type_active'])}' || lpad(active_num::text, {number_width}, '0')"
        )
        sql_lines.append(
            f"    INSERT INTO public.actives (id, active_number, id_design_number, id_location, "
            f"serial_number, lcn) VALUES ({act_ref}, {active_number_expr}, {vr['id_design_number']}, {loc_ref}, "
            f"{sn_val}, '{sql_escape(vr['lcn'])}'::ltree);"
        )

    sql_lines.append(
        f"    UPDATE public.iterator_number_last SET number = active_num "
        f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}';"
    )
    sql_lines.append("END $$;")

    return sql_lines


def delete_actives(valid_rows: list[dict]) -> list[str]:
    """Build the asset deletion SQL (without BEGIN/COMMIT), shared by download and execution.

    Per asset: its "empty" orders (directly and through maintenance — validation
    guarantees they have no related records), the ptoir_level_warning of its
    maintenance records and the ptoir rows themselves, the counter_active rows (created
    by the trigger on asset INSERT) and mileage_start (no FK — they would be orphaned
    otherwise), the asset itself, and then its location — but only when nothing else
    references it any more (other actives, materials, relocate); the NOT EXISTS check
    runs when the SQL executes, after the asset is already gone. Orders are deleted
    before ptoir because of the FK orders.id_ptoir -> ptoir.

    DELETE from counter_active is forbidden by the DBA trigger tr_abort_delete
    (dba.fn_abort_delete, an unconditional RAISE) — without disabling it temporarily an
    asset cannot be deleted at all: every asset gets a counter automatically, and the FK
    counter_active->actives blocks deleting the asset itself. The trigger is disabled
    only within this transaction (ALTER TABLE takes ACCESS EXCLUSIVE until it ends) and
    requires table owner privileges.
    """
    sql_lines: list[str] = [
        "ALTER TABLE public.counter_active DISABLE TRIGGER tr_abort_delete;"
    ]
    for vr in valid_rows:
        comment = f" -- '{sql_escape(vr['active_number'])}'"
        sql_lines.append(
            f"DELETE FROM public.orders WHERE id_active = {vr['id_active']} OR id_ptoir IN "
            f"(SELECT id FROM public.ptoir WHERE id_active = {vr['id_active']});{comment}"
        )
        sql_lines.append(
            f"DELETE FROM public.ptoir_level_warning WHERE id_ptoir IN "
            f"(SELECT id FROM public.ptoir WHERE id_active = {vr['id_active']});{comment}"
        )
        sql_lines.append(f"DELETE FROM public.ptoir WHERE id_active = {vr['id_active']};{comment}")
        sql_lines.append(f"DELETE FROM public.counter_active WHERE id_active = {vr['id_active']};{comment}")
        sql_lines.append(f"DELETE FROM public.mileage_start WHERE id_active = {vr['id_active']};{comment}")
        sql_lines.append(f"DELETE FROM public.actives WHERE id = {vr['id_active']};{comment}")
        if vr["id_location"] is not None:
            sql_lines.append(
                f"DELETE FROM public.location l WHERE l.id = {vr['id_location']} "
                f"AND NOT EXISTS (SELECT 1 FROM public.actives a WHERE a.id_location = l.id) "
                f"AND NOT EXISTS (SELECT 1 FROM public.materials m WHERE m.id_location = l.id) "
                f"AND NOT EXISTS (SELECT 1 FROM public.relocate r WHERE r.id_location_old = l.id "
                f"OR r.id_location_new = l.id);{comment}"
            )
    sql_lines.append("ALTER TABLE public.counter_active ENABLE TRIGGER tr_abort_delete;")
    return sql_lines


def create_named_actives(valid_rows: list[dict]) -> list[str]:
    """Build the body of the named asset creation SQL (without BEGIN/COMMIT).

    As in create_actives: ids for location/actives come from nextval(),
    and storage.last_lcn is read and locked FOR UPDATE inside the DO block as it runs.
    The difference is that active_number is inlined as a literal from the file and the
    iterator_number_last counter is not used.
    """
    total_actives = len(valid_rows)
    storage_ids = sorted({vr["id_storage"] for vr in valid_rows})

    sql_lines: list[str] = ["DO $$", "DECLARE"]
    sql_lines.append(
        f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
        f"FROM generate_series(1, {total_actives}));"
    )
    sql_lines.append(
        f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
        f"FROM generate_series(1, {total_actives}));"
    )
    for storage_id in storage_ids:
        sql_lines.append(f"    lcn_{storage_id} bigint;")

    sql_lines.append("BEGIN")
    for storage_id in storage_ids:
        sql_lines.append(
            f"    SELECT last_lcn INTO lcn_{storage_id} FROM public.storage WHERE id = {storage_id} FOR UPDATE;"
        )

    for i, vr in enumerate(valid_rows, start=1):
        loc_ref = f"loc_ids[{i}]"
        act_ref = f"act_ids[{i}]"
        lcn_var = f"lcn_{vr['id_storage']}"
        sql_lines.append(f"    {lcn_var} := {lcn_var} + 1;")
        sql_lines.append(
            f"    INSERT INTO public.location (id, id_type_location, id_storage, id_consignment) "
            f"VALUES ({loc_ref}, 1, {vr['id_storage']}, {vr['id_consignment']});"
        )
        sql_lines.append(
            f"    INSERT INTO public.actives (id, active_number, id_design_number, id_location, lcn) "
            f"VALUES ({act_ref}, '{sql_escape(vr['active_number'])}', {vr['id_design_number']}, {loc_ref}, "
            f"('S{vr['id_storage']}.' || {lcn_var})::ltree);"
        )

    for storage_id in storage_ids:
        sql_lines.append(f"    UPDATE public.storage SET last_lcn = lcn_{storage_id} WHERE id = {storage_id};")

    sql_lines.append("END $$;")

    return sql_lines
