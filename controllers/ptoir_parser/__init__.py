from .change_hours import ChangeHoursController
from .delete import DeletePtoirController
from .delete_main import DeleteMainPtoirController
from .launch import LaunchPtoirController
from .page import PtoirParserController

CONTROLLERS = [
    PtoirParserController,
    LaunchPtoirController,
    ChangeHoursController,
    DeletePtoirController,
    DeleteMainPtoirController,
]
