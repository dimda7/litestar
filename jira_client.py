import re

import httpx

from config import settings

REQUEST_TIMEOUT_SECONDS = 15

ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def _auth() -> tuple[str, str]:
    return settings.jira.user, settings.jira.password


async def get_issue(issue_key: str) -> dict:
    """Jira issue attachments and description: {attachments: [{id, filename, size, content_url}, ...], description}."""
    issue_key = issue_key.strip()
    if not ISSUE_KEY_RE.match(issue_key):
        raise ValueError(f"Некорректный номер задачи: '{issue_key}'")

    url = f"{settings.jira.base_url}/rest/api/2/issue/{issue_key}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, params={"fields": "attachment,description"}, auth=_auth())

    if resp.status_code == 404:
        raise ValueError(f"Задача '{issue_key}' не найдена")
    resp.raise_for_status()

    fields = resp.json().get("fields", {})
    attachments = fields.get("attachment", [])
    return {
        "attachments": [
            {"id": a["id"], "filename": a["filename"], "size": a["size"], "content_url": a["content"]}
            for a in attachments
        ],
        "description": fields.get("description") or "",
    }


async def download_attachment(content_url: str) -> tuple[bytes, str]:
    """Download an attachment by the content_url from list_attachments. Returns (content, mime_type).

    content_url must point at the configured Jira host — otherwise this endpoint
    becomes an open proxy to any address using our Jira credentials (SSRF).
    """
    if not content_url.startswith(settings.jira.base_url):
        raise ValueError("Недопустимый адрес вложения")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = await client.get(content_url, auth=_auth())
    resp.raise_for_status()

    return resp.content, resp.headers.get("content-type", "application/octet-stream")
