import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from models import Base, CarPlace, CounterGroup, DesignNumber, TrainType


@pytest_asyncio.fixture
async def db_session():
    """In-memory SQLite session standing in for the app's Postgres database.

    All ORM models are mapped with schema="public", and some controllers
    (train_parser) query it via raw `text("... FROM public.models")`. SQLite
    has no real schemas, but `ATTACH DATABASE ':memory:' AS public` registers
    an in-memory database under that name, so both ORM `select()` and raw
    schema-qualified SQL resolve against the same tables without diverging
    from the code under test.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _attach_public_schema(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("ATTACH DATABASE ':memory:' AS public")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


async def make_train_type(db_session: AsyncSession, name: str = "Ласточка") -> int:
    obj = TrainType(name=name)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_car_place(db_session: AsyncSession, name: str = "Вагон 1") -> int:
    obj = CarPlace(name=name)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_design_number(db_session: AsyncSession, number: str = "DN-001", **kwargs) -> int:
    obj = DesignNumber(number=number, **kwargs)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_counter_group(db_session: AsyncSession, name: str = "Группа 1") -> int:
    obj = CounterGroup(name=name)
    db_session.add(obj)
    await db_session.flush()
    return obj.id
