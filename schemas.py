from typing import Optional

import msgspec


class GenerateSQLRequest(msgspec.Struct):
    """Запрос на генерацию SQL для вставки строк в grom.models."""

    rows: str
    """JSON-массив строк из Excel. Каждая строка — объект с ключами model, position, itemnum, lsn/lcn, isdefault."""

    headers: str
    """JSON-массив заголовков столбцов из Excel."""


class DeleteRowsRequest(msgspec.Struct):
    """Запрос на генерацию SQL для удаления строк из grom.models."""

    rows: str
    """JSON-массив объектов с полем id — идентификаторы строк для удаления."""


class SelectSheetRequest(msgspec.Struct):
    """Запрос на выбор листа Excel."""

    sheet_name: str
    """Имя листа Excel для обработки."""


class SQLError(msgspec.Struct):
    """Ошибка валидации строки."""

    row: int
    """Номер строки с ошибкой (0 — общая ошибка)."""

    field: str
    """Поле, вызвавшее ошибку ('*' — вся строка)."""

    message: str
    """Описание ошибки."""


class GenerateSQLResponse(msgspec.Struct):
    """Ответ при генерации SQL-файла."""

    status: str
    """Статус операции: 'ok' или 'error'."""

    sql: Optional[str] = None
    """Сгенерированный SQL-код."""

    count: Optional[int] = None
    """Количество SQL-запросов."""

    errors: Optional[list[SQLError]] = None
    """Список ошибок валидации."""


class ExecuteSQLResponse(msgspec.Struct):
    """Ответ при выполнении SQL в БД."""

    status: str
    """Статус операции: 'ok' или 'error'."""

    count: Optional[int] = None
    """Количество вставленных/удалённых строк."""

    message: Optional[str] = None
    """Сообщение о результате."""

    errors: Optional[list[SQLError]] = None
    """Список ошибок."""


class LoginRequest(msgspec.Struct):
    """Запрос на аутентификацию."""

    username: str
    """Имя пользователя."""

    password: str
    """Пароль."""


class DesignNumberSelectSheetRequest(msgspec.Struct):
    """Запрос на выбор листа Excel для design_number парсера."""

    sheet_name: str
    """Имя листа Excel."""
