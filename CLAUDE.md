# Project Context

## Technology Stack
- Python 3.10+
- Litestar (latest stable)
- Uvicorn (ASGI server)
- Jinja2 (server-side HTML rendering)
- CSS: Tailwind CSS via CDN (no build step)
- Validation: Pydantic v2 (built into Litestar)
- Database: PostgreSQL via advanced_alchemy

## Communication Rules
- Respond in Russian
- Code, comments, commits — in English
- No fluff, no repeating the user's question

## Code Style
- Minimal readable code, follow project style
- Do not add docstrings/types/comments unless asked
- Do not refactor what wasn't requested
- No over-engineering

## Strict Rules
- **Typing**: All functions, parameters, and return values must have type annotations. Litestar uses them for DI and validation.
- **Async**: All route handlers must be `async def`.
- **Structure**: Group routes into controllers (`class MyController(Controller)`). Do not put everything in one file.
- **Templates**: Render HTML via `Template(template_name="...")`. Do not return raw HTML strings from controllers.
- **Static files**: Serve CSS/JS via `StaticFilesConfig` or `create_static_files_router`.
- **Errors**: Use custom exception handlers. Do not expose tracebacks in production.
- **Style**: PEP 8, 4-space indentation, clear variable names.

## Safety
- Never commit .env, secrets, tokens
- Never hardcode credentials
- No `rm -rf`, `DROP TABLE` without confirmation

## Forbidden
- ? Global variables for app state
- ? Hardcoded paths to templates/static
- ? Ignoring type hints

## Architecture Decisions
- Chosen Litestar over FastAPI/Django for built-in DI, strict typing, and high async performance.
- Server-side rendering via Jinja2 for SEO and fast first paint.
- Tailwind CDN for rapid prototyping without bundlers.
- `SESSION_SECRET` is persisted in `.env` (`Settings.session_secret`), never regenerated at startup — regenerating it used to invalidate every session on restart and diverge across multiple workers.
- CSRF protection via `CSRFConfig` (`litestar.config.csrf`), secret reused from `session_secret`. `base.html` exposes `csrf_token()` in a meta tag, which the `appendCsrfToken(formData)` helper in `static/js/api.js` reads for `fetch()` calls; plain forms use a hidden `_csrf_token` field.
- Excel-derived strings are passed through `sql_escape()` (`sql_utils.py`) before being interpolated into generated SQL — prevents injection/syntax breakage in the generated `.sql` files.
- Per-module logging via `logging_config.py` (`logging.config.dictConfig`): each module logger (`app`, `parser`, `train_parser`, `design_number_parser`) gets its own `RotatingFileHandler` under `log/<module>.log`. `LOG_LEVEL` is configurable via `.env`.
- SQL console query cancellation: the DB sits behind pgbouncer (`DB_PORT=6432`). Cancelling wraps execution in an `asyncio.Task` and calls `task.cancel()` so asyncpg sends a native Postgres `CancelRequest` — issuing a second SQL query on the pooled connection instead just queues behind the busy one and never cancels in time.
- DB connections live in `config_data/db_profiles.json` (`db_profiles.py`), not in `Settings` — they are editable at runtime from the Settings page, so a frozen dict built at import time would go stale. `DB_*` env vars only seed the file on first start (sets with a missing/non-numeric variable are skipped); afterwards `.env` no longer influences the list. The file is read on every access rather than cached: one uvicorn worker (`app.py` and the Docker `CMD` both run without `--workers`) means no cross-process races, and no cache means no stale-list bugs.
- Each connection is keyed by a uuid, never by its name — names are display-only, may repeat, and are freely editable without moving the active-connection pointer.
- `db_profiles.delete()` takes the active connection id as a **parameter** rather than asking `db_manager` for it: `db_manager` already imports `db_profiles`, so the reverse import would close a cycle. Both invariants (cannot delete the active connection, cannot delete the last one) therefore live in the module and are testable without mocks.
- Passwords are stored in plaintext, as they already were in `.env`; encrypting them with a key sitting in the same `.env` would add no security. The file is instead written atomically (`os.replace`) with mode `0600`, and a corrupt file degrades to an empty list — `load()` raising would 500 every page including the `/auth/db-select` recovery screen.
- `db_manager` keeps a **target epoch** counter, bumped whenever the app starts pointing at a different database (profile switch, or an edit to the active profile's host/port/dbname). `AuthMiddleware` compares it against `session["db_epoch"]` set at login. Without it, only the user who triggered the switch was logged out, while everyone else kept working in the new database under a `user_id` validated against the old one's `fdw_users`.
- `AuthMiddleware` answers unauthenticated **fetch** requests (`Sec-Fetch-Mode` != `navigate`) with `401` JSON instead of a `303` to the login page: `fetch()` follows the redirect silently and hands the JS the login page's HTML, where `resp.json()` dies with `Unexpected token '<'` instead of showing why. Plain form/page navigations still get the redirect. Client side, `readJsonResponse()` in `static/js/api.js` holds that contract — it redirects on `401` and reports a non-JSON body as "сервер ответил <status> без JSON" rather than a parser error; `postForm(url, formData)` and `getJson(url)` are its two entry points. `settings.html`, `parser.html` and the Jira helpers in `static/js/jira.js` go through them; the attachment download there cannot (it returns a file, not JSON) so it calls `throwIfUnauthorized()` directly — every `fetch()` in those files handles `401`. The remaining templates (`sql_console`, `train_parser`, `actives_parser`, `order_parser`, `ptoir_parser`, `design_number_parser`, `active_hierarchy`) still call `fetch().then(resp => resp.json())` directly — legacy debt, convert on touch.
- Long parser operations report progress through `progress_tasks.py` (`start_task(total, runner)` + `progress_response(task_id)`), one registry shared by every controller: task ids are uuids, so a single `_progress`/`_tasks` pair cannot collide. Handlers hand `start_task` a `lambda progress: self._run_x(progress, ...)` and the runner mutates that dict — it never looks the state up by task_id.
- The two biggest parser pages are split by operation into packages — `controllers/parser/` (insert/delete models, serial-none, change-lcn, is-default, move-no-relocate, move-actives) and `controllers/actives_parser/` (design-number, serial-number, recount-mileage, delete-actives, create-actives, create-named-actives, create-active-from-model). Every module holds one operation: its validation as a module-level function plus a `Controller` carrying only that operation's routes; `page.py` keeps the page itself (index/upload/select-sheet/progress) and `common.py` the package-wide bits (PREFIX, `parse_model_lcn`, the advanced_alchemy repositories). All classes of a package share the same `path` and are registered from its `CONTROLLERS` list in `app.py`, so the URLs are the same as when it was one class.
- Excel uploads go through `excel_upload.py` (`handle_upload`, `handle_sheet_choice`, `read_sheet`), which owns the whole flow: extension check, temp file, sheet picker, `parser_storage` handoff and the `<prefix>_error` / `<prefix>_pending_*` / `<prefix>_session_id` session keys — a controller only passes its prefix and page path. Two flags keep the pre-existing per-parser behaviour: `skip_blank_rows` (off for `parser`, `train_parser`, `design_number_parser` — dropping empty rows would shift the row numbers their validation errors quote) and `allow_sheet_choice=False` for `train_parser`, which has no sheet picker and always reads the active sheet.
- Generated SQL text lives in `sql_builders/` (`actives`, `models`, `train`, `ptoir`, `orders`, `design_number`), never in the controllers: the same builder feeds both "Скачать SQL-файл" and "Выполнить в базе данных" (plus the `log/*.log` echo), so the downloaded file and the executed statements cannot drift apart. Controllers import them as `from sql_builders import actives as actives_sql`. Constants the SQL bakes in (`ACTIVE_NUMBER_LENGTH`, `ACTIVE_NUMBER_COUNTER_DESCRIPTION`, `MILEAGE_COUNTER_TYPE_ID`) live in `sql_builders/actives.py` and are imported back into the controller — the reverse would close an import cycle. The one exception is `design_number_parser.generate_sql_counter_group`, where SQL lines are emitted inside a loop that queries the DB row by row.
- Connection URLs are built by `db_profiles.build_url()`, which percent-encodes user and password — a typed password like `p@ss` otherwise makes SQLAlchemy parse the host as `ss@…`.

## Litestar Specifics
- Jinja2: `TemplateConfig(engine=JinjaTemplateEngine(directory="templates"))`
- Static files: `StaticFilesConfig(directories=["static"], path="/static")` passed as list in `static_files_config=[...]`
- `litestar run --reload` works correctly only if entrypoint imports without side effects
- In Litestar 2.24: `Template` argument is `template_name`, not `name`
- Jinja2 imports: `from litestar.contrib.jinja import JinjaTemplateEngine` + `from litestar.template.config import TemplateConfig`
- Static files import: `from litestar.static_files import StaticFilesConfig` (not `litestar.config.static_files`)
- File uploads: DI parsing of `UploadFile` via function parameter does not work — use `await request.form()` and `.get("file")` directly
- Exception handlers registered in `exception_handlers` must be synchronous `def`, not `async def` — Litestar calls them without `await`

## Testing
- Tests substitute Postgres with in-memory SQLite: `ATTACH DATABASE ':memory:' AS public` (+ `StaticPool`) on the `connect` event, since models and raw SQL both target `schema="public"`.
- Postgres-specific SQL (e.g. `::text` casts in `train_parser.py`) is not covered by these tests — needs a real Postgres (testcontainers or similar) for full coverage.

## Known Bugs / Data Issues
- When looking up `CarPlace` by `name`, use `.scalars().all()` and handle 0/1/many explicitly — `.scalar_one_or_none()` raises `MultipleResultsFound` if duplicate names ever reappear in DB

## Agent skills

### Issue tracker

Issues live in GitHub Issues (dimda7/litestar), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.
