from dataclasses import dataclass, fields

from sql_utils import sql_escape


@dataclass(frozen=True)
class DesignNumberRow:
    id: int | None
    number: str
    description: str | None
    id_unit_type: int | None
    is_serial: bool | None
    is_created_1c: bool | None
    id_commodity_group: int | None
    id_commodity_code: int | None
    id_design_number_status: int | None
    id_measure_type: int | None
    is_repairable: bool | None
    id_product_group: int | None
    description_1c: str | None
    main_number: str | None
    id_counter_group: int | None
    is_serial_1c: bool | None
    maint_use_ids: tuple[int, ...]


def update_counter_group(valid_rows: list[tuple[int, str, int]]) -> list[str]:
    return [
        f"UPDATE design_number SET id_counter_group = {cg_id} WHERE number = '{sql_escape(number)}';"
        for _, number, cg_id in valid_rows
    ]


def update_unit_type(valid_rows: list[tuple[int, str, int]]) -> list[str]:
    return [
        f"UPDATE design_number SET id_unit_type = {ut_id} WHERE number = '{sql_escape(number)}';"
        for _, number, ut_id in valid_rows
    ]


def update_is_serial_1c(valid_rows: list[tuple[int, str, bool]]) -> list[str]:
    return [
        f"UPDATE design_number SET is_serial_1c = {'TRUE' if is_serial_1c else 'FALSE'} "
        f"WHERE number = '{sql_escape(number)}';"
        for _, number, is_serial_1c in valid_rows
    ]


def rename(valid_rows: list[tuple[int, str, str]]) -> list[str]:
    return [
        f"UPDATE design_number SET number = '{sql_escape(new)}' WHERE number = '{sql_escape(old)}';"
        for _, old, new in valid_rows
    ]


def _literal(value: str | int | bool | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    return f"'{sql_escape(value)}'"


def _data_columns(row: DesignNumberRow) -> list[tuple[str, str | int | bool | None]]:
    return [
        (field.name, getattr(row, field.name))
        for field in fields(row)
        if field.name not in ("id", "number", "maint_use_ids")
    ]


def _insert_links(row: DesignNumberRow, on_conflict: str = "") -> list[str]:
    number = _literal(row.number)
    return [
        f"INSERT INTO design_number_to_maint_use (id_design_number, id_maint_use) "
        f"SELECT id, {id_maint_use} FROM design_number WHERE number = {number}{on_conflict};"
        for id_maint_use in row.maint_use_ids
    ]


def update_full(rows: list[DesignNumberRow]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        number = _literal(row.number)
        assignments = ", ".join(f"{column} = {_literal(value)}" for column, value in _data_columns(row))
        lines.append(f"UPDATE design_number SET {assignments} WHERE number = {number};")
        id_design_number = f"(SELECT id FROM design_number WHERE number = {number})"
        kept = f" AND id_maint_use NOT IN ({', '.join(map(str, row.maint_use_ids))})" if row.maint_use_ids else ""
        lines.append(f"DELETE FROM design_number_to_maint_use WHERE id_design_number = {id_design_number}{kept};")
        lines.extend(_insert_links(row, " ON CONFLICT DO NOTHING"))
    return lines


def create(rows: list[DesignNumberRow]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        columns = _data_columns(row)
        names = ", ".join(column for column, _ in columns)
        values = ", ".join(_literal(value) for _, value in columns)
        lines.append(f"INSERT INTO design_number (number, {names}) VALUES ({_literal(row.number)}, {values});")
        lines.extend(_insert_links(row))
    return lines


def delete_created(rows: list[DesignNumberRow]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        number = _literal(row.number)
        lines.append(
            f"DELETE FROM design_number_to_maint_use "
            f"WHERE id_design_number = (SELECT id FROM design_number WHERE number = {number});"
        )
        lines.append(f"DELETE FROM design_number WHERE number = {number};")
    return lines


def create_or_update(rows: list[DesignNumberRow]) -> list[str]:
    return create([row for row in rows if row.id is None]) + update_full([row for row in rows if row.id is not None])


def undo_create_or_update(rows: list[DesignNumberRow], snapshot: list[DesignNumberRow]) -> list[str]:
    return delete_created([row for row in rows if row.id is None]) + update_full(snapshot)
