from .page import ActivesParserController
from .design_number import DesignNumberController
from .serial_number import SerialNumberController
from .recount_mileage import RecountMileageController
from .delete_actives import DeleteActivesController
from .create_named_actives import CreateNamedActivesController
from .create_actives import CreateActivesController
from .create_active_from_model import CreateActiveFromModelController

CONTROLLERS = [
    ActivesParserController,
    DesignNumberController,
    SerialNumberController,
    RecountMileageController,
    DeleteActivesController,
    CreateNamedActivesController,
    CreateActivesController,
    CreateActiveFromModelController,
]
