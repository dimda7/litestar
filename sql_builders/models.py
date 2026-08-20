from datetime import datetime

from sql_utils import sql_escape


def two_phase_lcn_update(valid_rows: list[dict], extra_set: str = "") -> list[str]:
    """Two-phase lcn UPDATE: old -> temporary ('Z'+old) -> new.

    Files often contain chains (one pair's new lcn equals another pair's old
    one — a child position moving after its parent, say). Updating in a
    single FROM(VALUES...) statement fails with UniqueViolationError on
    actives_lcn_key: until every row has moved, the intermediate state holds
    a duplicate. The temporary 'Z' prefix cannot collide with real lcns
    (train_id, 'M' model ones, 'S' storage ones) — after the first pass no
    live lcn matches another pair's old or new value.

    extra_set holds additional ", column = value" clauses for the final
    UPDATE (resetting id_actves_parent/id_actives_root on a move, say).
    """
    if not valid_rows:
        return []
    old_list = ", ".join(f"'{sql_escape(vr['old_lcn'])}'" for vr in valid_rows)
    tmp_values_list = ", ".join(
        f"('Z{sql_escape(vr['old_lcn'])}', '{sql_escape(vr['new_lcn'])}')" for vr in valid_rows
    )
    return [
        f"UPDATE public.actives SET lcn = ('Z' || lcn::text)::ltree WHERE lcn::text IN ({old_list});",
        f"UPDATE public.actives AS act SET lcn = v.new_lcn::ltree{extra_set} "
        f"FROM (VALUES {tmp_values_list}) AS v(tmp_lcn, new_lcn) WHERE act.lcn::text = v.tmp_lcn;",
    ]


def move_no_relocate(valid_rows: list[dict]) -> list[str]:
    """The same two-phase lcn UPDATE as in 'Изменить lcn в модели' in spirit,
    but on actives here (no id_location change and no relocate)."""
    return two_phase_lcn_update(valid_rows)


def change_model_lcn(valid_rows: list[dict]) -> list[str]:
    """Two-phase UPDATE of public.models.lcn, matching on models.id.

    Unlike the actives variant, matching goes through the stable id
    (models.id does not change, lcn does) rather than the lcn text — the id
    alone identifies the row. The 'Z' prefix is still needed: lcn is part of
    a composite UNIQUE (id_train_type, lcn, id_car_place, id_design_number,
    is_default), and the per-row processing order within one UPDATE is not
    guaranteed — without a temporary value chains can raise
    UniqueViolationError (see two_phase_lcn_update).
    """
    if not valid_rows:
        return []
    id_list = ", ".join(str(vr["id"]) for vr in valid_rows)
    values_list = ", ".join(f"({vr['id']}, '{sql_escape(vr['new_lcn'])}')" for vr in valid_rows)
    return [
        f"UPDATE public.models SET lcn = ('Z' || lcn::text)::ltree WHERE id IN ({id_list});",
        "UPDATE public.models AS m SET lcn = v.new_lcn::ltree "
        f"FROM (VALUES {values_list}) AS v(mid, new_lcn) WHERE m.id = v.mid;",
    ]


def change_model_okz(valid_rows: list[dict]) -> list[str]:
    """Two-phase UPDATE of public.models.id_car_place, matching on models.id.

    id_car_place is part of the same composite UNIQUE (id_train_type, lcn,
    id_car_place, id_design_number, is_default) that change_model_lcn works
    around, so a swap chain (row A -> row B's old place, row B -> row A's old
    place) can hit UniqueViolationError inside one bulk UPDATE just like lcn
    does. id_car_place is a nullable integer FK though, not text, so there is
    no prefix to prepend — a plain NULL stands in for the 'Z' prefix trick,
    since Postgres treats NULL as distinct for UNIQUE purposes.
    """
    if not valid_rows:
        return []
    id_list = ", ".join(str(vr["id"]) for vr in valid_rows)
    values_list = ", ".join(f"({vr['id']}, {vr['new_car_place_id']})" for vr in valid_rows)
    return [
        f"UPDATE public.models SET id_car_place = NULL WHERE id IN ({id_list});",
        "UPDATE public.models AS m SET id_car_place = v.new_car_place "
        f"FROM (VALUES {values_list}) AS v(mid, new_car_place) WHERE m.id = v.mid;",
    ]


def change_okz_active(valid_rows: list[dict]) -> list[str]:
    """Single-statement UPDATE of public.location.id_car_place for assets found
    by their per-train lcn (see 'изменить okz в активе по модели').

    public.actives has no id_car_place column of its own — it lives on
    public.location, reached through actives.id_location — so the update is
    joined through actives on lcn, same matching key set_serial_none already
    uses. No composite UNIQUE on location/actives involves id_car_place
    (unlike models'), so unlike change_model_okz this needs no two-phase NULL
    trick: one bulk UPDATE is safe. An lcn_train with no matching asset (not
    every train has every position) simply matches no row, not an error.
    """
    values_list = ", ".join(
        f"('{sql_escape(lcn_train)}', {vr['new_car_place_id']})"
        for vr in valid_rows for lcn_train in vr["lcn_trains"]
    )
    if not values_list:
        return []
    return [
        "UPDATE public.location AS loc SET id_car_place = v.new_car_place "
        f"FROM public.actives AS act, (VALUES {values_list}) AS v(lcn_train, new_car_place) "
        "WHERE act.lcn::text = v.lcn_train AND loc.id = act.id_location;"
    ]


def set_is_default(valid_rows: list[dict]) -> list[str]:
    """FALSE rows go before TRUE ones: the partial UNIQUE index (WHERE is_default=true)

    is checked row by row as the individual UPDATEs run inside one transaction
    rather than at the end — so if a file clears the old default and sets a new one
    at the same (lcn, car_place) or (car_place, train_type, design_number), the
    clearing must run before the setting.
    """
    ordered = sorted(valid_rows, key=lambda vr: vr["is_default"])
    return [
        f"UPDATE public.models SET is_default = {'TRUE' if vr['is_default'] else 'FALSE'} WHERE id = {vr['id']};"
        for vr in ordered
    ]


def merge_serial_none_lcns(valid_rows: list[dict]) -> list[str]:
    """Merge the lcn_trains of every file row into one duplicate-free list.

    Different Excel rows with the same lsn (or different lsns yielding
    overlapping lcns) would otherwise produce several identical or
    overlapping UPDATEs in a row — this makes it one query for the whole file.
    """
    seen: set[str] = set()
    merged: list[str] = []
    for vr in valid_rows:
        for l in vr["lcn_trains"]:
            if l not in seen:
                seen.add(l)
                merged.append(l)
    return merged


def set_serial_none(valid_rows: list[dict]) -> list[str]:
    lcns = merge_serial_none_lcns(valid_rows)
    if not lcns:
        return []
    lcn_list = ", ".join(f"'{sql_escape(l)}'" for l in lcns)
    return [f"UPDATE public.actives SET serial_number = 'none' WHERE lcn::text IN ({lcn_list});"]


def move_actives(
    valid_rows: list[dict], id_storage: int, id_consignment: int, id_user: int,
    reason: str, move_date: datetime, id_design_number: int | None = None,
) -> list[str]:
    """Build the DO block moving assets into storage (the move_active equivalent).

    One destination storage for the whole batch (unlike the original script,
    where 'Куда' was taken per row) — which reduces the lcn counter to a
    single variable instead of a dict keyed by storage id. The lcn is taken
    from storage.last_lcn under FOR UPDATE and written back at the end of the
    DO block (unlike the original move_active, which incremented the counter
    only in Python memory and never saved it back — a rerun would hand out
    the same lcn numbers again).

    id_design_number (the "Установить позицию ТМЦ = 'NOCM'" checkbox), when
    set, is additionally written by the same UPDATE on actives.
    """
    total = len(valid_rows)
    date_str = move_date.strftime("%Y-%m-%d %H:%M:%S")
    reason_val = f"'{sql_escape(reason)}'" if reason else "NULL"

    sql_lines: list[str] = ["DO $$", "DECLARE"]
    sql_lines.append(
        f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
        f"FROM generate_series(1, {total}));"
    )
    sql_lines.append("    lcn_new bigint;")
    sql_lines.append("BEGIN")
    sql_lines.append(f"    SELECT last_lcn INTO lcn_new FROM public.storage WHERE id = {id_storage} FOR UPDATE;")

    for i, vr in enumerate(valid_rows, start=1):
        loc_ref = f"loc_ids[{i}]"
        old_loc_val = str(vr["id_location_old"]) if vr["id_location_old"] is not None else "NULL"
        sql_lines.append("    lcn_new := lcn_new + 1;")
        sql_lines.append(
            f"    INSERT INTO public.location (id, id_type_location, id_storage, id_consignment) "
            f"VALUES ({loc_ref}, 1, {id_storage}, {id_consignment});"
        )
        sql_lines.append(
            f"    INSERT INTO public.relocate (id_location_old, id_location_new, date, id_user, id_active, "
            f"reason, date_current, id_order) VALUES ({old_loc_val}, {loc_ref}, '{date_str}', {id_user}, "
            f"{vr['id_active']}, {reason_val}, '{date_str}', NULL);"
        )
        design_number_clause = f"id_design_number = {id_design_number}, " if id_design_number is not None else ""
        active_number_comment = str(vr["active_number"]).replace("\n", " ").replace("\r", " ")
        sql_lines.append(
            f"    UPDATE public.actives SET id_location = {loc_ref}, id_actves_parent = NULL, "
            f"id_actives_root = NULL, {design_number_clause}lcn = ('S{id_storage}.' || lcn_new)::ltree "
            f"WHERE id = {vr['id_active']}; -- {sql_escape(active_number_comment)}"
        )

    sql_lines.append(f"    UPDATE public.storage SET last_lcn = lcn_new WHERE id = {id_storage};")
    sql_lines.append("END $$;")
    return sql_lines


def insert_models(valid_rows: list[tuple[int, int, int, str, bool]]) -> list[str]:
    sql_lines: list[str] = []
    for train_type_id, car_place_id, design_number_id, lcn, is_default in valid_rows:
        isdefault_val = "TRUE" if is_default else "FALSE"
        sql_lines.append(
            f"INSERT INTO public.models (id_train_type, id_car_place, id_design_number, lcn, is_default) "
            f"VALUES ({train_type_id}, {car_place_id}, {design_number_id}, '{sql_escape(lcn)}', {isdefault_val});"
        )
    return sql_lines


def delete_models(valid_ids: list[int]) -> list[str]:
    return [f"DELETE FROM public.models WHERE id = {rid};" for rid in valid_ids]
