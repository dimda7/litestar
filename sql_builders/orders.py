from sql_utils import sql_escape


def assign_parent_order(valid_rows: list[tuple[int, int, str, str]]) -> list[str]:
    sql_lines: list[str] = []
    for child_id, parent_id, child_number, parent_number in valid_rows:
        sql_lines.append(
            f"INSERT INTO public.order_to_order (id_parent, id_child) VALUES ({parent_id}, {child_id}) "
            f"ON CONFLICT (id_child) DO UPDATE SET id_parent = EXCLUDED.id_parent; "
            f"-- '{sql_escape(child_number)}' -> '{sql_escape(parent_number)}'"
        )
    return sql_lines
