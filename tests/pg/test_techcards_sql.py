"""'Техкарты' against a real PostgreSQL copy.

The techcard and its status reference each other, which SQLite cannot show
the generated SQL getting right. Reference rows (work type, classifier type,
specifications) are the copy's own RUS_INSPECTION ones.

The downloadable BEGIN; ... COMMIT; variant is not run here — its COMMIT would
end the fixture's transaction.
"""
from sqlalchemy import text

from controllers.techcard_parser.create import validate_techcard_rows
from controllers.techcard_parser.operations import validate_operation_rows
from sql_builders import techcards as techcards_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX


def make_row(name: str, **overrides) -> dict:
    row = {
        "Номер техкарты": name,
        "Статус": "ACTIVE",
        "Название техкарты": "Инспекция I5",
        "Полное описание": "<p>O'Brien: :1 &quot;</p>",
        "Тип работы": "PM",
        "Приоритет": 3,
        "Бригада": "SERVICE",
        "Использовать техкарту только в БПТОиР?": "Да",
        "Классификация техкарты": "RUS_INSPECTION",
        "TRAIN_TYPE_MAINT": "ЭС1",
        "INSPECTION_TYPE": "И5",
        "SIZE": "Большой",
    }
    row.update(overrides)
    return row


async def test_techcards_are_created_with_status_and_specifications(pg_session):
    names = [f"{TEST_PREFIX}-tc-1", f"{TEST_PREFIX}-tc-2"]
    rows = [make_row(names[0]),
            make_row(names[1], **{"Полное описание": None, "Бригада": None, "Приоритет": None, "SIZE": None,
                                  "Использовать техкарту только в БПТОиР?": "Нет"})]
    errors, valid_rows = await validate_techcard_rows(pg_session, rows)
    assert errors == []

    await run_generated_sql(pg_session, "\n".join(techcards_sql.create(valid_rows)))

    created = (await pg_session.execute(text(
        "SELECT t.name, t.description, t.description_full, w.name AS work_type, p.priority, g.name AS brigade, "
        "       t.use_only_main_ptoir, ct.name AS classifier_type, t.use_for_order, t.id_users, "
        "       st.status, s.techcard_number = t.id AS status_points_back, s.id_users AS status_user, "
        "       s.date_status, "
        "       (SELECT array_agg(sp.description ORDER BY sp.description) "
        "          FROM public.techcard_to_specification tts "
        "          JOIN public.specification sp ON sp.id = tts.id_specification "
        "         WHERE tts.id_techcard = t.id) AS specifications "
        "FROM public.techcard t "
        "JOIN public.work_type w ON w.id = t.id_work_type "
        "LEFT JOIN public.priorities p ON p.id = t.id_priorities "
        "LEFT JOIN public.group_type g ON g.id = t.id_group_type "
        "JOIN public.classifier_type ct ON ct.id = t.id_classifier_type "
        "JOIN public.techcard_status s ON s.id = t.id_techcard_status "
        "JOIN public.techcard_status_types st ON st.id = s.id_techcard_status_types "
        "WHERE t.name = ANY(:names) ORDER BY t.name"), {"names": names})).mappings().all()

    moscow_now = await pg_session.scalar(text("SELECT now() AT TIME ZONE 'Europe/Moscow'"))
    assert [{k: v for k, v in r.items() if k != "date_status"} for r in created] == [
        {"name": names[0], "description": "Инспекция I5", "description_full": "<p>O'Brien: :1 &quot;</p>",
         "work_type": "PM", "priority": 3, "brigade": "SERVICE", "use_only_main_ptoir": True,
         "classifier_type": "RUS_INSPECTION", "use_for_order": True, "id_users": 505,
         "status": "ACTIVE", "status_points_back": True, "status_user": 505,
         "specifications": ["Большой", "И5", "ЭС1"]},
        {"name": names[1], "description": "Инспекция I5", "description_full": None,
         "work_type": "PM", "priority": None, "brigade": None, "use_only_main_ptoir": False,
         "classifier_type": "RUS_INSPECTION", "use_for_order": True, "id_users": 505,
         "status": "ACTIVE", "status_points_back": True, "status_user": 505,
         "specifications": ["И5", "ЭС1"]},
    ]
    assert all(r["date_status"] == moscow_now for r in created)


async def test_operations_are_added_to_a_techcard(pg_session):
    head = f"{TEST_PREFIX}-tc-ops"
    errors, valid_rows = await validate_techcard_rows(pg_session, [make_row(head)])
    assert errors == []
    await run_generated_sql(pg_session, "\n".join(techcards_sql.create(valid_rows)))

    errors, valid_rows = await validate_operation_rows(pg_session, [
        {"Техкарта": head, "Номер операции в техкарте": 10, "Наименование операции": "Проверить O'Brien :1",
         "Вложенная техкарта": None, "Описание вложенной техкарты": None},
        {"Техкарта": head, "Номер операции в техкарте": 40, "Наименование операции": "Замеры КП",
         "Вложенная техкарта": "USI", "Описание вложенной техкарты": None},
        {"Техкарта": head, "Номер операции в техкарте": None, "Наименование операции": None,
         "Вложенная техкарта": None, "Описание вложенной техкарты": None},
    ])
    assert errors == []

    await run_generated_sql(pg_session, "\n".join(techcards_sql.add_operations(valid_rows)))

    created = (await pg_session.execute(text(
        "SELECT w.task, w.task_name, e.name AS nested, w.working_hours, w.comment "
        "FROM public.techcard_work_operation w "
        "JOIN public.techcard h ON h.id = w.id_techcard_head "
        "LEFT JOIN public.techcard e ON e.id = w.id_techcard_early "
        "WHERE h.name = :head ORDER BY w.task"), {"head": head})).mappings().all()
    assert [dict(r) for r in created] == [
        {"task": 10, "task_name": "Проверить O'Brien :1", "nested": None, "working_hours": 0, "comment": None},
        {"task": 40, "task_name": "Замеры КП", "nested": "USI", "working_hours": 0, "comment": None},
    ]


async def test_techcard_without_status(pg_session):
    name = f"{TEST_PREFIX}-tc-no-status"
    errors, valid_rows = await validate_techcard_rows(pg_session, [make_row(name, **{"Статус": None})])
    assert errors == []

    await run_generated_sql(pg_session, "\n".join(techcards_sql.create(valid_rows)))

    created = (await pg_session.execute(text(
        "SELECT t.id_techcard_status, t.id_users, "
        "       (SELECT count(*) FROM public.techcard_status s WHERE s.techcard_number = t.id) AS statuses, "
        "       (SELECT count(*) FROM public.techcard_to_specification tts WHERE tts.id_techcard = t.id) AS specs "
        "FROM public.techcard t WHERE t.name = :name"), {"name": name})).mappings().one()
    assert dict(created) == {"id_techcard_status": None, "id_users": 505, "statuses": 0, "specs": 3}
