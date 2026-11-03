from sql_utils import sql_escape

TECHCARD_USER_ID = 505


def _literal(value: str | int | bool | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f"'{sql_escape(value)}'"


def create(valid_rows: list[dict]) -> list[str]:
    # The techcard and its status point at each other. Both ids are taken from
    # the sequences up front: a sibling CTE cannot see the row another one
    # inserted, so the techcard cannot be updated with the status id afterwards.
    sql_lines: list[str] = []
    for vr in valid_rows:
        columns = {
            "name": vr["name"],
            "description": vr["description"],
            "description_full": vr["description_full"],
            "id_work_type": vr["id_work_type"],
            "id_priorities": vr["id_priorities"],
            "id_group_type": vr["id_group_type"],
            "use_only_main_ptoir": vr["use_only_main_ptoir"],
            "id_classifier_type": vr["id_classifier_type"],
            "use_for_order": True,
            "id_users": TECHCARD_USER_ID,
        }
        specifications = ""
        if vr["specification_ids"]:
            values = ", ".join(f"({id_specification})" for id_specification in vr["specification_ids"])
            specifications = (
                ", spec AS (INSERT INTO public.techcard_to_specification (id_techcard, id_specification) "
                f"SELECT ids.id_techcard, v.id FROM ids, (VALUES {values}) AS v(id))"
            )
        sql_lines.append(
            "WITH ids AS (SELECT nextval('public.techcard_id_seq') AS id_techcard, "
            "nextval('public.techcard_status_id_seq') AS id_status), "
            "status AS (INSERT INTO public.techcard_status "
            "(id, techcard_number, id_techcard_status_types, date_status, id_users) "
            f"SELECT id_status, id_techcard, {vr['id_status_type']}, now() AT TIME ZONE 'Europe/Moscow', "
            f"{TECHCARD_USER_ID} FROM ids)"
            f"{specifications} "
            f"INSERT INTO public.techcard (id, id_techcard_status, {', '.join(columns)}) "
            f"SELECT id_techcard, id_status, {', '.join(_literal(v) for v in columns.values())} FROM ids;"
        )
    return sql_lines
