from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from schemas import SelectSheetRequest
import excel_upload
from progress_tasks import progress_response

from .common import PREFIX


class ActivesParserController(Controller):
    path = "/actives-parser"

    @get("/")
    async def index(
        self,
        request: Request,
        page: int = 1,
        per_page: int = 10,
        select_sheet: bool = False,
    ) -> Template:
        page = max(page, 1)
        per_page = min(per_page, 200)
        error: str = request.session.pop(f"{PREFIX}_error", "")

        pending_sheets: list[str] = []
        pending_filename: str = ""
        if select_sheet:
            pending_sheets = request.session.get(f"{PREFIX}_pending_sheets", [])
            pending_filename = request.session.get(f"{PREFIX}_pending_filename", "")

        stored = excel_upload.stored_data(request, PREFIX)

        all_rows: list[dict] = stored["rows"] if stored else []
        headers: list[str] = stored["headers"] if stored else []
        filename: str = stored["filename"] if stored else ""

        total = len(all_rows)
        total_pages = max((total + per_page - 1) // per_page, 1)
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page
        rows = all_rows[offset:offset + per_page]

        return Template(
            template_name="actives_parser.html",
            context={
                "headers": headers,
                "rows": rows,
                "all_rows": all_rows,
                "filename": filename,
                "error": error,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "actives_parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        """Upload of an Excel file (.xlsx/.xls) — see excel_upload.handle_upload."""
        return await excel_upload.handle_upload(request, PREFIX, "/actives-parser", skip_blank_rows=True)

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: SelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """Sheet choice for a multi-sheet Excel file — see excel_upload.handle_sheet_choice."""
        return excel_upload.handle_sheet_choice(request, PREFIX, "/actives-parser", data.sheet_name, skip_blank_rows=True)

    @get("/progress/{task_id:str}")
    async def task_progress(self, task_id: str) -> Response:
        return progress_response(task_id)
