from datetime import date, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from models import (
    Actives, Base, CarPlace, Consignment, CounterActive, CounterGroup, CounterType, DesignNumber,
    MileageTrain, Orders, Ptoir, PtoirLevelWarning, Storage, Train, TrainType, UnitType, User,
)


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


async def make_unit_type(db_session: AsyncSession, name: str = "Ось колесной пары") -> int:
    obj = UnitType(name=name)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_counter_type(db_session: AsyncSession, type_name: str = "Пробег") -> int:
    obj = CounterType(type=type_name)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_active(db_session: AsyncSession, active_number: str = "SPV000001", **kwargs) -> int:
    obj = Actives(active_number=active_number, **kwargs)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_ptoir(db_session: AsyncSession, number_ptoir: str = "ТО0001", id_active: int | None = None) -> int:
    obj = Ptoir(number_ptoir=number_ptoir, id_active=id_active)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_ptoir_level_warning(db_session: AsyncSession, id_ptoir: int, id_counter_type: int) -> int:
    obj = PtoirLevelWarning(id_ptoir=id_ptoir, id_counter_type=id_counter_type)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_order(db_session: AsyncSession, order_number: str = "ЗН000001") -> int:
    obj = Orders(order_number=order_number)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_train(db_session: AsyncSession, id_train_type: int, name: str = "Поезд 1", **kwargs) -> int:
    obj = Train(id_train_type=id_train_type, name=name, **kwargs)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_storage(db_session: AsyncSession, name: str = "Виртуальный склад", last_lcn: int = 0) -> int:
    obj = Storage(name=name, last_lcn=last_lcn)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_consignment(db_session: AsyncSession, name: str = "ЧСП ЛОМ") -> int:
    obj = Consignment(name=name)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_user(
    db_session: AsyncSession, lastname: str = "Велебская", firstname: str = "Александра", middlename: str | None = "Владимировна",
) -> int:
    obj = User(lastname=lastname, firstname=firstname, middlename=middlename)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_mileage_train(
    db_session: AsyncSession, id_train: int, date_average: date, milage: int | None,
    date: datetime | None = None, mileage_average: int = 0,
) -> int:
    obj = MileageTrain(id_train=id_train, milage=milage, mileage_average=mileage_average,
                       date=date, date_average=date_average)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_counter_active(
    db_session: AsyncSession, id_active: int, value: int = 0, id_counter_type: int = 3,
    is_train: bool = True, date: datetime | None = None,
) -> int:
    obj = CounterActive(id_active=id_active, value=value, id_counter_type=id_counter_type,
                        is_train=is_train, date=date or datetime(2023, 1, 1))
    db_session.add(obj)
    await db_session.flush()
    return obj.id
