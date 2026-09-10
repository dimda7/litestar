import re
from collections.abc import Iterable

from sql_utils import sql_escape


def parse_car_number(position: str) -> int | None:
    """Parse the car number out of a car place name: '+100_(01)' -> 1."""
    if not position:
        return None
    match = re.search(r"_\((\d+)\)", position)
    if match:
        return int(match.group(1))
    return None


def new_names(refs: Iterable[int | str]) -> list[str]:
    """The car places a file names but the DB lacks, once each, in file order.

    A validated row refers to its car place either by id (it exists) or by
    name (a str: it has to be created)."""
    return list(dict.fromkeys(ref for ref in refs if isinstance(ref, str)))


def insert_missing(refs: Iterable[int | str]) -> list[str]:
    """One idempotent INSERT per car place to create.

    ON CONFLICT keeps a downloaded file runnable when someone creates the same
    name between download and run; RETURNING tells the execute path which
    names this run actually created."""
    lines: list[str] = []
    for name in new_names(refs):
        car_number = parse_car_number(name)
        car_number_val = str(car_number) if car_number is not None else "NULL"
        lines.append(
            f"INSERT INTO public.car_place (name, car_number) VALUES ('{sql_escape(name)}', {car_number_val}) "
            "ON CONFLICT (name) DO NOTHING RETURNING name;"
        )
    return lines


def ref_sql(ref: int | str) -> str:
    """An existing car place by its id; one created by insert_missing by its
    name — its id is unknown when the file is generated."""
    if isinstance(ref, str):
        return f"(SELECT id FROM public.car_place WHERE name = '{sql_escape(ref)}')"
    return str(ref)
