from .page import PtoirParserController
from .launch import LaunchPtoirController
from .change_hours import ChangeHoursController
from .delete import DeletePtoirController
from .delete_main import DeleteMainPtoirController

CONTROLLERS = [
    PtoirParserController,
    LaunchPtoirController,
    ChangeHoursController,
    DeletePtoirController,
    DeleteMainPtoirController,
]
