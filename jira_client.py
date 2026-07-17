import re

import httpx

from config import settings

REQUEST_TIMEOUT_SECONDS = 15

ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def _auth() -> tuple[str, str]:
    return settings.jira.user, settings.jira.password


async def list_attachments(issue_key: str) -> list[dict]:
    """Вложения задачи Jira: [{id, filename, size, content_url}, ...]."""
    issue_key = issue_key.strip()
    if not ISSUE_KEY_RE.match(issue_key):
        raise ValueError(f"Некорректный номер задачи: '{issue_key}'")

    url = f"{settings.jira.base_url}/rest/api/2/issue/{issue_key}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, params={"fields": "attachment"}, auth=_auth())

    if resp.status_code == 404:
        raise ValueError(f"Задача '{issue_key}' не найдена")
    resp.raise_for_status()

    attachments = resp.json().get("fields", {}).get("attachment", [])
    return [
        {"id": a["id"], "filename": a["filename"], "size": a["size"], "content_url": a["content"]}
        for a in attachments
    ]


async def download_attachment(content_url: str) -> tuple[bytes, str]:
    """Скачивает вложение по content_url из list_attachments. Возвращает (содержимое, mime_type).

    content_url обязан вести на настроенный Jira-хост — иначе этот эндпоинт
    превращается в открытый прокси на произвольный адрес через наши Jira-креды (SSRF).
    """
    if not content_url.startswith(settings.jira.base_url):
        raise ValueError("Недопустимый адрес вложения")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = await client.get(content_url, auth=_auth())
    resp.raise_for_status()

    return resp.content, resp.headers.get("content-type", "application/octet-stream")
