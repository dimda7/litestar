from .counter_group import CounterGroupController
from .create import CreateDesignNumberController
from .is_serial_1c import IsSerial1cController
from .page import DesignNumberParserController
from .rename import RenameDesignNumberController
from .sync_counter_active import SyncCounterActiveController
from .unit_type import UnitTypeController
from .update import UpdateDesignNumberController

CONTROLLERS = [
    DesignNumberParserController,
    CounterGroupController,
    SyncCounterActiveController,
    UnitTypeController,
    IsSerial1cController,
    RenameDesignNumberController,
    UpdateDesignNumberController,
    CreateDesignNumberController,
]
