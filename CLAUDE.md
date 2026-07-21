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
- Code, comments, commits � in English
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
- CSRF protection via `CSRFConfig` (`litestar.config.csrf`), secret reused from `session_secret`. `base.html` exposes `csrf_token()` in a meta tag plus a JS `appendCsrfToken(formData)` helper for `fetch()` calls; plain forms use a hidden `_csrf_token` field.
- Excel-derived strings are passed through `sql_escape()` (`sql_utils.py`) before being interpolated into generated SQL — prevents injection/syntax breakage in the generated `.sql` files.
- Per-module logging via `logging_config.py` (`logging.config.dictConfig`): each module logger (`app`, `parser`, `train_parser`, `design_number_parser`) gets its own `RotatingFileHandler` under `log/<module>.log`. `LOG_LEVEL` is configurable via `.env`.
- SQL console query cancellation: the DB sits behind pgbouncer (`DB_PORT=6432`). Cancelling wraps execution in an `asyncio.Task` and calls `task.cancel()` so asyncpg sends a native Postgres `CancelRequest` — issuing a second SQL query on the pooled connection instead just queues behind the busy one and never cancels in time.

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
EOF