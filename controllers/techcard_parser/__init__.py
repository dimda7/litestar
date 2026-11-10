from .create import CreateTechcardsController
from .operations import AddOperationsController
from .page import TechcardParserController

CONTROLLERS = [
    TechcardParserController,
    CreateTechcardsController,
    AddOperationsController,
]
