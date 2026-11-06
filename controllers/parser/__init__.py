from .page import ParserController
from .insert_models import InsertModelsController
from .delete_models import DeleteModelsController
from .serial_none import SerialNoneController
from .change_lcn import ChangeModelLcnController
from .change_okz import ChangeModelOkzController
from .change_design_number import ChangeModelDesignNumberController
from .change_okz_active import ChangeOkzActiveController
from .change_design_number_active import ChangeDesignNumberActiveController
from .is_default import IsDefaultController
from .move_no_relocate import MoveNoRelocateController
from .move_actives import MoveActivesController

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
