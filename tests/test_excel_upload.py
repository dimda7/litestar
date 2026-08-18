from pathlib import Path

import openpyxl
import pytest

import excel_upload
import parser_storage


class FakeUpload:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


class FakeRequest:
    def __init__(self, upload: FakeUpload | None = None) -> None:
        self.session: dict = {}
        self._upload = upload

    async def form(self) -> dict:
        return {"file": self._upload}


@pytest.fixture(autouse=True)
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(parser_storage, "PARSER_DATA_DIR", tmp_path)
    return tmp_path


def make_workbook(path: Path, sheets: dict[str, list[list]]) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)


def stored_data(request: FakeRequest, prefix: str) -> dict:
    session_id = request.session[f"{prefix}_session_id"]
    return parser_storage.load_data(session_id)


def test_read_sheet_uses_first_row_as_headers(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Лист1": [["Актив", "Позиция"], ["ES1", "A2V1"]]})

    headers, rows = excel_upload.read_sheet(str(path))

    assert headers == ["Актив", "Позиция"]
    assert rows == [{"Актив": "ES1", "Позиция": "A2V1"}]


def test_read_sheet_names_empty_header_by_position(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Лист1": [["Актив", None], ["ES1", "x"]]})

    headers, _ = excel_upload.read_sheet(str(path))

    assert headers == ["Актив", "col_1"]


def test_read_sheet_keeps_blank_rows_by_default(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Лист1": [["Актив"], ["ES1"], [None], ["ES2"]]})

    _, rows = excel_upload.read_sheet(str(path))

    assert rows == [{"Актив": "ES1"}, {"Актив": None}, {"Актив": "ES2"}]


def test_read_sheet_skips_blank_rows_when_asked(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Лист1": [["Актив"], ["ES1"], [None], ["ES2"]]})

    _, rows = excel_upload.read_sheet(str(path), skip_blank_rows=True)

    assert rows == [{"Актив": "ES1"}, {"Актив": "ES2"}]


def test_read_sheet_reads_the_named_sheet(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Первый": [["A"], ["1"]], "Второй": [["B"], ["2"]]})

    headers, rows = excel_upload.read_sheet(str(path), "Второй")

    assert (headers, rows) == (["B"], [{"B": "2"}])


async def test_upload_without_a_file_reports_an_error():
    request = FakeRequest(upload=None)

    response = await excel_upload.handle_upload(request, "parser", "/parser")

    assert request.session["parser_error"] == "Файл не выбран"
    assert "parser_session_id" not in request.session


async def test_upload_rejects_a_non_excel_extension():
    request = FakeRequest(FakeUpload("data.csv", b""))

    await excel_upload.handle_upload(request, "parser", "/parser")

    assert request.session["parser_error"] == "Поддерживаются только .xlsx и .xls файлы"


async def test_upload_of_a_single_sheet_file_stores_the_rows(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Лист1": [["Актив"], ["ES1"]]})
    request = FakeRequest(FakeUpload("поезда.xlsx", path.read_bytes()))

    await excel_upload.handle_upload(request, "parser", "/parser")

    assert stored_data(request, "parser") == {
        "headers": ["Актив"], "rows": [{"Актив": "ES1"}], "filename": "поезда.xlsx",
    }
    assert "parser_pending_file" not in request.session


async def test_upload_of_a_multi_sheet_file_defers_to_the_picker(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Первый": [["A"], ["1"]], "Второй": [["B"], ["2"]]})
    request = FakeRequest(FakeUpload("f.xlsx", path.read_bytes()))

    await excel_upload.handle_upload(request, "parser", "/parser")

    assert request.session["parser_pending_sheets"] == ["Первый", "Второй"]
    assert Path(request.session["parser_pending_file"]).exists()
    assert "parser_session_id" not in request.session


async def test_upload_ignores_extra_sheets_when_the_picker_is_off(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Первый": [["A"], ["1"]], "Второй": [["B"], ["2"]]})
    request = FakeRequest(FakeUpload("f.xlsx", path.read_bytes()))

    await excel_upload.handle_upload(request, "train_parser", "/train-parser", allow_sheet_choice=False)

    assert stored_data(request, "train_parser")["rows"] == [{"A": "1"}]
    assert "train_parser_pending_file" not in request.session


async def test_sheet_choice_parses_the_chosen_sheet_and_clears_the_pending_state(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Первый": [["A"], ["1"]], "Второй": [["B"], ["2"]]})
    request = FakeRequest(FakeUpload("f.xlsx", path.read_bytes()))
    await excel_upload.handle_upload(request, "parser", "/parser")
    tmp_file = request.session["parser_pending_file"]

    excel_upload.handle_sheet_choice(request, "parser", "/parser", "Второй")

    assert stored_data(request, "parser") == {
        "headers": ["B"], "rows": [{"B": "2"}], "filename": "f.xlsx [Второй]",
    }
    assert "parser_pending_file" not in request.session
    assert not Path(tmp_file).exists()


async def test_sheet_choice_rejects_a_sheet_missing_from_the_file(tmp_path):
    path = tmp_path / "f.xlsx"
    make_workbook(path, {"Первый": [["A"], ["1"]], "Второй": [["B"], ["2"]]})
    request = FakeRequest(FakeUpload("f.xlsx", path.read_bytes()))
    await excel_upload.handle_upload(request, "parser", "/parser")

    excel_upload.handle_sheet_choice(request, "parser", "/parser", "Третий")

    assert request.session["parser_error"] == "Выбранный лист не найден в файле."
    assert "parser_session_id" not in request.session


def test_sheet_choice_after_the_temporary_file_expired():
    request = FakeRequest()
    request.session["parser_pending_file"] = "/nonexistent/f.xlsx"

    excel_upload.handle_sheet_choice(request, "parser", "/parser", "Лист1")

    assert request.session["parser_error"] == "Временный файл истёк. Загрузите файл заново."
