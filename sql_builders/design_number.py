from sql_utils import sql_escape


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
