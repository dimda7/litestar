"""'Запуск ПТОиР' against a real PostgreSQL copy.

The page executes exactly what ptoir.update_ptoir builds (through sql_tasks), so
the test runs that SQL and checks what it leaves in ptoir / ptoir_level_warning:
the Moscow-time Excel dates land shifted to UTC, and the ПТОиР is switched on.
The downloadable BEGIN; ... COMMIT; variant is not run — its COMMIT would end
the fixture's transaction.
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.ptoir_parser.launch import validate_launch_rows
from sql_builders import ptoir as ptoir_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX


# The page posts its rows as JSON: parser_storage stores cells with default=str, so a date
# cell arrives as '2026-07-13 12:00:00', and a typed one as text in whatever form it was typed.
@pytest.mark.parametrize("activation", [datetime(2026, 7, 13, 12, 0), "2026-07-13 12:00:00", "13.07.2026 12:00"])
async def test_launch_sets_activation_interval_and_zero_point(
    pg_session: AsyncSession,
    activation: datetime | str,
) -> None:
    id_counter_type, counter_type = (
        await pg_session.execute(text("SELECT id, type FROM public.counter_type ORDER BY id LIMIT 1"))
    ).one()
    number = f"{TEST_PREFIX}-launch"
    id_ptoir = await pg_session.scalar(
        text("INSERT INTO public.ptoir (number_ptoir, is_active) VALUES (:n, false) RETURNING id"), {"n": number}
    )
    id_warning = await pg_session.scalar(
        text("INSERT INTO public.ptoir_level_warning (id_ptoir, id_counter_type) VALUES (:p, :t) RETURNING id"),
        {"p": id_ptoir, "t": id_counter_type},
    )

    errors, valid_rows = await validate_launch_rows(
        pg_session,
        [
            {
                "ПТОиР": number,
                "Актив": "",
                "Тип счетчика": counter_type,
                "Интервал": 30,
                "Дата активации": activation,
                "Данные последнего обслуживания": 1000,
            }
        ],
    )
    assert errors == []

    await run_generated_sql(pg_session, "\n".join(ptoir_sql.update_ptoir(valid_rows)))

    ptoir = (
        await pg_session.execute(
            text("SELECT date_activation, interval, is_active FROM public.ptoir WHERE id = :p"), {"p": id_ptoir}
        )
    ).one()
    assert tuple(ptoir) == (datetime(2026, 7, 13, 9, 0), 30, True)
    assert (
        await pg_session.scalar(
            text("SELECT zero_point_value FROM public.ptoir_level_warning WHERE id = :w"), {"w": id_warning}
        )
        == 1000
    )
