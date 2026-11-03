from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
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
    id_train_series: Mapped[int | None] = mapped_column(Integer, nullable=True)


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


class UnitType(Base):
    __tablename__ = "unit_type"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)


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


class Storage(Base):
    __tablename__ = "storage"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    id_depot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_lcn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    is_delete: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    id_1c: Mapped[str | None] = mapped_column(String, nullable=True)


class StoragePlace(Base):
    __tablename__ = "storage_place"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    is_delete: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)


class Consignment(Base):
    __tablename__ = "consignment"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    is_delete: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    is_malfunctioned: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    on_balance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class IteratorNumberLast(Base):
    __tablename__ = "iterator_number_last"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)


class Relocate(Base):
    __tablename__ = "relocate"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_location_old: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_location_new: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    id_user: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    id_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_root_active: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Orders(Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str | None] = mapped_column(String, nullable=True)
    id_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_ptoir: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Techcard(Base):
    __tablename__ = "techcard"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    description_full: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_priorities: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_work_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    working_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_classifier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_group_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_for_order: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_techcard_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_classifier_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_add_to_train_form: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_techcard_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name_short: Mapped[str | None] = mapped_column(String, nullable=True)
    is_report_filtr_rzd: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_only_main_ptoir: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_locked: Mapped[bool | None] = mapped_column(Boolean, default=False)
    planned_labor_results: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WorkType(Base):
    __tablename__ = "work_type"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)


class GroupType(Base):
    __tablename__ = "group_type"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)


class Priorities(Base):
    __tablename__ = "priorities"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)


class ClassifierType(Base):
    __tablename__ = "classifier_type"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)


class Classifier(Base):
    __tablename__ = "classifier"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    id_classifier_type: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Specification(Base):
    __tablename__ = "specification"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    id_classifier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_delete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class TechcardStatusTypes(Base):
    __tablename__ = "techcard_status_types"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)


class TechcardStatus(Base):
    __tablename__ = "techcard_status"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    techcard_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_techcard_status_types: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_status: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    id_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)


class TechcardToSpecification(Base):
    __tablename__ = "techcard_to_specification"
    __table_args__ = {"schema": "public"}

    id_techcard: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_specification: Mapped[int] = mapped_column(Integer, primary_key=True)


class TechcardWorkOperation(Base):
    __tablename__ = "techcard_work_operation"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task: Mapped[int] = mapped_column(Integer)
    task_name: Mapped[str] = mapped_column(String)
    id_techcard_head: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_techcard_early: Mapped[int | None] = mapped_column(Integer, nullable=True)
    working_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)


class OrderToActives(Base):
    __tablename__ = "order_to_actives"
    __table_args__ = {"schema": "public"}

    id_order: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_active: Mapped[int] = mapped_column(Integer, primary_key=True)


class OrderToOrder(Base):
    __tablename__ = "order_to_order"
    __table_args__ = {"schema": "public"}

    id_parent: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_child: Mapped[int] = mapped_column(Integer, primary_key=True)


class ActiveAdditionalField(Base):
    __tablename__ = "active_additional_field"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    id_active: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ActivesToMainPtoir(Base):
    __tablename__ = "actives_to_main_ptoir"
    __table_args__ = {"schema": "public"}

    id_main_ptoir: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_actives: Mapped[int] = mapped_column(Integer, primary_key=True)


class MaterialsToActives(Base):
    __tablename__ = "materials_to_actives"
    __table_args__ = {"schema": "public"}

    id_materials: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_actives: Mapped[int] = mapped_column(Integer, primary_key=True)


class MileageHistoryActives(Base):
    __tablename__ = "mileage_history_actives"
    __table_args__ = {"schema": "public"}

    id_mileage: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_actives: Mapped[int] = mapped_column(Integer, primary_key=True)


class MileageStart(Base):
    __tablename__ = "mileage_start"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    milage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date: Mapped[str | None] = mapped_column(Date, nullable=True)
    is_recount: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    milage_const: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
