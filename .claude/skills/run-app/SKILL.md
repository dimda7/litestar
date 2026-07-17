---
name: run-app
description: How to launch the Grom Litestar app locally for manual/browser verification
---

# Running the app

- Entrypoint: `app.py`, app object `app`. Requires `.env` in project root (DB_*, SESSION_SECRET, JIIRA_*, optional SERVER_PORT).
- Use the project venv: `venv/bin/python`.
- Start the server:

```bash
cd /my_hdd/ii/litestar && venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8011
```

- Default port is 8011 (`SERVER_PORT` env var overrides it). App is served at http://localhost:8011.
- For auto-reload during development add `--reload`.
- Most pages require login (AuthMiddleware, session cookie); the auth flow is in `controllers/auth.py`.
- UI is server-rendered Jinja2 (`templates/`) with Tailwind via CDN — verify UI changes in the browser, not just via HTTP status codes.
