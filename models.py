from sqlalchemy import Boolean, Date, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "fdw_users"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    firstname: Mapped[str | None] = mapped_column(String, nullable=True)
    lastname: Mapped[str | None] = mapped_column(String, nullable=True)
    middlename: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone_number: Mapped[str | None] = mapped_column(String, nullable=True)
    mail: Mapped[str | None] = mapped_column(String, nullable=True)
    is_user: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_role: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String, nullable=True)


class TrainType(Base):
    __tablename__ = "train_type"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)


class CarPlace(Base):
    __tablename__ = "car_place"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)


class DesignNumber(Base):
    __tablename__ = "design_number"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    id_unit_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_counter_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_serial_1c: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)


class CounterGroup(Base):
    __tablename__ = "counter_group"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)


class Models(Base):
    __tablename__ = "models"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_train_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lcn: Mapped[str | None] = mapped_column(String, nullable=True)
    id_car_place: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_design_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_activated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class Train(Base):
    __tablename__ = "train"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_direction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_train_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_mileage_last: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_depot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    is_delete: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    count_car: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_train_series: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Location(Base):
    __tablename__ = "location"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_type_location: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_train: Mapped[int | None] = mapped_column(Integer, nullable=True)
    car_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_car_place: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_storage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_storage_place: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_train_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_consignment: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Actives(Base):
    __tablename__ = "actives"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    active_number: Mapped[str | None] = mapped_column(String, nullable=True)
    id_unit_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_design_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_location: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_product_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_vendor_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String, nullable=True)
    id_mileage_last: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lcn: Mapped[str | None] = mapped_column(String, nullable=True)
    id_actves_parent: Mapped[str | None] = mapped_column(String, nullable=True)
    id_actives_root: Mapped[str | None] = mapped_column(String, nullable=True)
    id_materials: Mapped[int | None] = mapped_column(Integer, nullable=True)
    special_account: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    bdi: Mapped[str | None] = mapped_column(String, nullable=True, default="GREY")
    id_status: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CounterType(Base):
    __tablename__ = "counter_type"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)


class Ptoir(Base):
    __tablename__ = "ptoir"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number_ptoir: Mapped[str | None] = mapped_column(String, nullable=True)
    id_main_ptoir: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_ptoir_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_activation: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    bdi: Mapped[str | None] = mapped_column(String, nullable=True, default="GREY")


class PtoirLevelWarning(Base):
    __tablename__ = "ptoir_level_warning"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    id_ptoir: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_counter_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zero_point_value: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MileageTrain(Base):
    __tablename__ = "mileage_train"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_train: Mapped[int] = mapped_column(Integer, nullable=False)
    milage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mileage_average: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    date: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    date_average: Mapped[str] = mapped_column(Date, nullable=False)


class CounterActive(Base):
    __tablename__ = "counter_active"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_active: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[str | None] = mapped_column(DateTime, nullable=False)
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_counter_type: Mapped[int] = mapped_column(Integer, nullable=False)
    id_frequency_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_train: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    value_source: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    id_turning_codes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_create: Mapped[str | None] = mapped_column(DateTime, nullable=True)
