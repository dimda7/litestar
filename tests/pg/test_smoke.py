from sqlalchemy import text

from tests.pg.factories import make_train, reference_ids


async def test_connection_is_a_grom_copy(pg_session):
    assert await pg_session.scalar(text("SELECT count(*) FROM public.actives")) > 0


async def test_writes_never_leave_the_test_transaction(pg_session, pg_engine):
    """The isolation the whole directory depends on: a second connection must not
    see anything a test wrote, because the transaction is only ever rolled back."""
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    count = text("SELECT count(*) FROM public.train WHERE id = :id")

    assert await pg_session.scalar(count, {"id": id_train}) == 1
    async with pg_engine.connect() as other:
        assert (await other.execute(count, {"id": id_train})).scalar_one() == 0
