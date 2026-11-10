from .change_okz import ChangeOkzController
from .create_active_from_model import CreateActiveFromModelController
from .create_actives import CreateActivesController
from .create_named_actives import CreateNamedActivesController
from .delete_actives import DeleteActivesController
from .design_number import DesignNumberController
from .page import ActivesParserController
from .recount_mileage import RecountMileageController
from .serial_number import SerialNumberController

CONTROLLERS = [
    ActivesParserController,
    DesignNumberController,
    SerialNumberController,
    ChangeOkzController,
    RecountMileageController,
    DeleteActivesController,
    CreateNamedActivesController,
    CreateActivesController,
    CreateActiveFromModelController,
]
