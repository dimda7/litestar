import re

from advanced_alchemy.repository import SQLAlchemyAsyncRepository

from models import Actives, CounterActive, Consignment, DesignNumber, IteratorNumberLast, MileageStart, Storage, StoragePlace


PREFIX = "actives_parser"


# A model lcn like 'M9.1.6.4': the digits after the letter prefix and before the
# first dot are id_train_type; the rest of the path is carried over as is.
# The same pattern as in controllers/parser/common.py (parse_model_lcn) — not reused
# from there directly, since the project's controllers are self-contained.
_MODEL_LCN_RE = re.compile(r"^\D*(\d+)(?:\.(.*))?$")


def parse_model_lcn(lcn: str) -> tuple[int, str] | None:
    """Extract (id_train_type, rest_of_path) from an lcn like 'M9.6.5' -> (9, '6.5'); 'M9' -> (9, '')."""
    match = _MODEL_LCN_RE.match(lcn)
    if not match:
        return None
    return int(match.group(1)), match.group(2) or ""


class StorageRepository(SQLAlchemyAsyncRepository[Storage]):
    model_type = Storage


class StoragePlaceRepository(SQLAlchemyAsyncRepository[StoragePlace]):
    model_type = StoragePlace


class ConsignmentRepository(SQLAlchemyAsyncRepository[Consignment]):
    model_type = Consignment


class DesignNumberRepository(SQLAlchemyAsyncRepository[DesignNumber]):
    model_type = DesignNumber


class IteratorNumberLastRepository(SQLAlchemyAsyncRepository[IteratorNumberLast]):
    model_type = IteratorNumberLast


class ActivesRepository(SQLAlchemyAsyncRepository[Actives]):
    model_type = Actives


class MileageStartRepository(SQLAlchemyAsyncRepository[MileageStart]):
    model_type = MileageStart


class CounterActiveRepository(SQLAlchemyAsyncRepository[CounterActive]):
    model_type = CounterActive
