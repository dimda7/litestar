import json
import logging

from litestar import Controller, get
from litestar.response import Response

import jira_client

logger = logging.getLogger("jira")


class JiraController(Controller):
    path = "/jira"

    @get("/attachments")
    async def list_attachments(self, issue: str) -> Response:
        try:
            attachments = await jira_client.list_attachments(issue)
        except ValueError as e:
            return Response(
                content=json.dumps({"status": "error", "message": str(e)}),
                status_code=200,
                media_type="application/json",
            )
        except Exception as e:
            logger.warning("Jira attachments lookup failed for %s: %s", issue, e)
            return Response(
                content=json.dumps({"status": "error", "message": f"Ошибка обращения к Jira: {e}"}),
                status_code=200,
                media_type="application/json",
            )

        return Response(
            content=json.dumps({"status": "ok", "attachments": attachments}),
            status_code=200,
            media_type="application/json",
        )

    @get("/attachments/download")
    async def download_attachment(self, content_url: str) -> Response:
        try:
            content, mime_type = await jira_client.download_attachment(content_url)
        except ValueError as e:
            return Response(content=str(e), status_code=400)
        except Exception as e:
            logger.warning("Jira attachment download failed for %s: %s", content_url, e)
            return Response(content=f"Ошибка обращения к Jira: {e}", status_code=502)

        return Response(content=content, media_type=mime_type)
