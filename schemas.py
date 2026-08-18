from typing import Optional

import msgspec


class GenerateSQLRequest(msgspec.Struct):
    """Request to generate SQL inserting rows into grom.models."""

    rows: str
    """JSON array of Excel rows. Each row is an object with the keys model, position, itemnum, lsn/lcn, isdefault."""

    headers: str
    """JSON array of column headers from Excel."""


class DeleteRowsRequest(msgspec.Struct):
    """Request to generate SQL deleting rows from grom.models."""

    rows: str
    """JSON array of objects with an id field — the rows to delete."""


class SelectSheetRequest(msgspec.Struct):
    """Request selecting an Excel sheet."""

    sheet_name: str
    """Name of the Excel sheet to process."""


class SQLError(msgspec.Struct):
    """A row validation error."""

    row: int
    """Number of the offending row (0 means a general error)."""

    field: str
    """Field that caused the error ('*' means the whole row)."""

    message: str
    """Description of the error."""


class GenerateSQLResponse(msgspec.Struct):
    """Response for SQL file generation."""

    status: str
    """Operation status: 'ok' or 'error'."""

    sql: Optional[str] = None
    """The generated SQL."""

    count: Optional[int] = None
    """Number of SQL statements."""

    errors: Optional[list[SQLError]] = None
    """List of validation errors."""


class ExecuteSQLResponse(msgspec.Struct):
    """Response for executing SQL against the database."""

    status: str
    """Operation status: 'ok' or 'error'."""

    count: Optional[int] = None
    """Number of rows inserted or deleted."""

    message: Optional[str] = None
    """Result message."""

    errors: Optional[list[SQLError]] = None
    """List of errors."""


class LoginRequest(msgspec.Struct):
    """Authentication request."""

    username: str
    """User name."""

    password: str
    """Password."""


class DesignNumberSelectSheetRequest(msgspec.Struct):
    """Request selecting an Excel sheet for the design_number parser."""

    sheet_name: str
    """Name of the Excel sheet."""


class DBProfileRequest(msgspec.Struct):
    """Request to test, switch to, or delete a database connection."""

    profile: str
    """Connection id (a uuid from db_profiles.json)."""


class DbSelectRequest(msgspec.Struct):
    """Database choice made on /auth/db-select, before logging in."""

    profile: str
    """Connection id."""


class DBProfileFormRequest(msgspec.Struct):
    """Fields of the DB connection form (adding, and testing unsaved parameters).

    The port arrives as a string: db_profiles parses it and checks the range,
    so the error comes back through the shared JSON contract rather than as a
    400 from Litestar's validator.
    """

    name: str
    host: str
    port: str
    user: str
    password: str
    dbname: str


class DBProfileUpdateRequest(DBProfileFormRequest):
    """The same fields plus the id of the connection being edited."""

    profile: str = ""
