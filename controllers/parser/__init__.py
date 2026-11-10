from .change_design_number import ChangeModelDesignNumberController
from .change_design_number_active import ChangeDesignNumberActiveController
from .change_lcn import ChangeModelLcnController
from .change_okz import ChangeModelOkzController
from .change_okz_active import ChangeOkzActiveController
from .delete_models import DeleteModelsController
from .insert_models import InsertModelsController
from .is_default import IsDefaultController
from .move_actives import MoveActivesController
from .move_no_relocate import MoveNoRelocateController
from .page import ParserController
from .serial_none import SerialNoneController

CONTROLLERS = [
    ParserController,
    InsertModelsController,
    DeleteModelsController,
    SerialNoneController,
    ChangeModelLcnController,
    ChangeModelOkzController,
    ChangeModelDesignNumberController,
    ChangeOkzActiveController,
    ChangeDesignNumberActiveController,
    IsDefaultController,
    MoveNoRelocateController,
    MoveActivesController,
]
