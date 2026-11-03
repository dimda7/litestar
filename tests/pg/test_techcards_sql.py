"""'Техкарты' against a real PostgreSQL copy.

The techcard and its status reference each other, which SQLite cannot show
the generated SQL getting right. Reference rows (work type, classifier type,
specifications) are the copy's own RUS_INSPECTION ones.

The downloadable BEGIN; ... COMMIT; variant is not run here — its COMMIT would
end the fixture's transaction.
"""
from sqlalchemy import text

from controllers.techcard_parser import validate_techcard_rows
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
