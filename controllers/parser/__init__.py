from .page import ParserController
from .insert_models import InsertModelsController
from .delete_models import DeleteModelsController
from .serial_none import SerialNoneController
from .change_lcn import ChangeModelLcnController
from .is_default import IsDefaultController
from .move_no_relocate import MoveNoRelocateController
from .move_actives import MoveActivesController

CONTROLLERS = [
    ParserController,
    InsertModelsController,
    DeleteModelsController,
    SerialNoneController,
    ChangeModelLcnController,
    IsDefaultController,
    MoveNoRelocateController,
    MoveActivesController,
]
