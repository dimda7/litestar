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
- Per-module logging via `logging_config.py` (`logging.config.dictConfig`): each module logger in `MODULE_LOGGERS` gets its own `RotatingFileHandler` under `log/<module>.log`. `LOG_LEVEL` is configurable via `.env`.
- SQL console query cancellation: the DB sits behind pgbouncer (`DB_PORT=6432`). Cancelling wraps execution in an `asyncio.Task` and calls `task.cancel()` so asyncpg sends a native Postgres `CancelRequest` — issuing a second SQL query on the pooled connection instead just queues behind the busy one and never cancels in time.
- DB connections live in `config_data/db_profiles.json` (`db_profiles.py`), not in `Settings` — they are editable at runtime from the Settings page, so a frozen dict built at import time would go stale. `DB_*` env vars only seed the file on first start (sets with a missing/non-numeric variable are skipped); afterwards `.env` no longer influences the list. The file is read on every access rather than cached: one uvicorn worker (`app.py` and the Docker `CMD` both run without `--workers`) means no cross-process races, and no cache means no stale-list bugs.
- Each connection is keyed by a uuid, never by its name — names are display-only, may repeat, and are freely editable without moving the active-connection pointer.
- `db_profiles.delete()` takes the active connection id as a **parameter** rather than asking `db_manager` for it: `db_manager` already imports `db_profiles`, so the reverse import would close a cycle. Both invariants (cannot delete the active connection, cannot delete the last one) therefore live in the module and are testable without mocks.
- Passwords are stored in plaintext, as they already were in `.env`; encrypting them with a key sitting in the same `.env` would add no security. The file is instead written atomically (`os.replace`) with mode `0600`, and a corrupt file degrades to an empty list — `load()` raising would 500 every page including the `/auth/db-select` recovery screen.
- Connection URLs are built by `db_profiles.build_url()`, which percent-encodes user and password — a typed password like `p@ss` otherwise makes SQLAlchemy parse the host as `ss@…`.
- `db_manager` keeps a **target epoch** counter, bumped whenever the app starts pointing at a different database (profile switch, or an edit to the active profile's host/port/dbname). `AuthMiddleware` compares it against `session["db_epoch"]` set at login. Without it, only the user who triggered the switch was logged out, while everyone else kept working in the new database under a `user_id` validated against the old one's `fdw_users`.
- `AuthMiddleware` answers unauthenticated **fetch** requests (`Sec-Fetch-Mode` != `navigate`) with `401` JSON instead of a `303` to the login page: `fetch()` follows the redirect silently and hands the JS the login page's HTML, where `resp.json()` dies with `Unexpected token '<'` instead of showing why. Plain form/page navigations still get the redirect. Client side, `readJsonResponse()` in `static/js/api.js` holds that contract — it redirects on `401` and reports a non-JSON body as "сервер ответил <status> без JSON" rather than a parser error; `postForm(url, formData)` and `getJson(url)` are its two entry points. Every template and `static/js/jira.js` go through them; two calls cannot and handle `401` themselves: the Jira attachment download (it returns a file, not JSON) calls `throwIfUnauthorized()`, and the SQL console's cancel (it needs an `AbortController` signal for its timeout) does `fetch(...).then(readJsonResponse)`. Never call `resp.json()` on a `fetch()` directly.
- Long parser operations report progress through `progress_tasks.py` (`start_task(total, runner)` + `progress_response(task_id)`), one registry shared by every controller: task ids are uuids, so a single `_progress`/`_tasks` pair cannot collide. Handlers hand `start_task` a `lambda progress: self._run_x(progress, ...)` (operations on `sql_tasks`: a closure built by `start_generate` / `start_execute`) and the runner mutates that dict — it never looks the state up by task_id.
- The generate/execute flow of an operation whose SQL comes whole from a builder lives in `sql_tasks.py` (issue #20): an operation is a `SqlOperation` (validator, builder, log name/title, `summary(rows_in_file, valid_rows, errors) -> (message, log_lines)`, empty message, logger, `partial`), and its routes are one-liners `sql_tasks.start_generate(data, OP)` / `start_execute(data, OP)` (rows posted in the form) or `generate_rows(rows, OP)` / `execute_rows(rows, OP)` (rows from `excel_upload.stored_rows`). Execution sends the builder's lines over the raw asyncpg connection in one transaction, split into up to `EXECUTE_PROGRESS_STEPS` (100) batches so the progress bar counts statements instead of jumping 0→1 — which requires every builder line to be one whole statement; the log is written only after the commit. `per_statement=True` runs the statements one at a time over the SQLAlchemy connection (`exec_driver_sql`) and hands `summary` each one's rowcount, for messages that say how many rows were touched; otherwise `rowcounts` is empty. `partial=True` ("Удалить ПТОиР") returns row errors alongside the result, as `-- строка N пропущена` comments in the generated file, instead of blocking it. On it: `actives_parser` change-okz, serial-number, design-number, delete-actives and recount-mileage, both techcards operations and every ПТОиР operation ("Запуск ПТОиР" — whose valid rows are tuples, hence the untyped `list` — "Изменение моточасов", "Удалить ПТОиР", "Удалить БПТОиР"). "Запуск ПТОиР" used to run parameterised `UPDATE`s row by row and only log `update_ptoir`; since it runs the builder's SQL, `tests/pg/test_ptoir_launch_sql.py` pins what that SQL writes. Only operations that fit as they are move onto it (user decision): ones with a multi-element `DO $$` builder, an extra validator result, a post-execution query, a rollback logged before the run, or a synchronous page (`design_number_parser`) keep their own flow rather than growing `SqlOperation` a flag per case. `tests/test_sql_tasks.py` drives the module on SQLite through `progress_response`, with `get_session_maker` and `LOG_DIR` swapped for the test's; the SQLite driver takes one statement per batch, so those builders emit one. `tests/pg/test_sql_tasks_pg.py` runs `execute_rows` on the grom copy inside `pg_session`, for what only asyncpg can show (the `per_statement` rowcount).
- The parser pages with several operations are split by operation into packages — `controllers/parser/` (insert/delete models, serial-none, change-lcn, is-default, move-no-relocate, move-actives), `controllers/ptoir_parser/` (launch, change-hours, delete, delete-main), `controllers/design_number_parser/` (counter-group, sync-counter-active, unit-type, is-serial-1c, rename, update, create; `common.py` also holds the 16-column field rules and `snapshot_design_numbers` shared by update and create) and `controllers/actives_parser/` (design-number, serial-number, recount-mileage, delete-actives, create-actives, create-named-actives, create-active-from-model). Every module holds one operation: its validation as a module-level function plus a `Controller` carrying only that operation's routes; `page.py` keeps the page itself (index/upload/select-sheet/progress) and `common.py` the package-wide bits (PREFIX, `parse_model_lcn`, the advanced_alchemy repositories). All classes of a package share the same `path` and are registered from its `CONTROLLERS` list in `app.py`, so the URLs are the same as when it was one class.
- Excel uploads go through `excel_upload.py` (`handle_upload`, `handle_sheet_choice`, `read_sheet`), which owns the whole flow: extension check, temp file, sheet picker, `parser_storage` handoff and the `<prefix>_error` / `<prefix>_pending_*` / `<prefix>_session_id` session keys — a controller only passes its prefix and page path. Two flags keep the pre-existing per-parser behaviour: `skip_blank_rows` (off for `parser`, `train_parser`, `design_number_parser` — dropping empty rows would shift the row numbers their validation errors quote) and `allow_sheet_choice=False` for `train_parser`, which has no sheet picker and always reads the active sheet.
- Generated SQL text lives in `sql_builders/` (`actives`, `models`, `car_place`, `train`, `ptoir`, `orders`, `design_number`, `mileage`), never in the controllers: the same builder feeds both "Скачать SQL-файл" and "Выполнить в базе данных" (plus the `log/*.log` echo), so the downloaded file and the executed statements cannot drift apart. Controllers import them as `from sql_builders import actives as actives_sql`. Constants the SQL bakes in (`ACTIVE_NUMBER_LENGTH`, `ACTIVE_NUMBER_COUNTER_DESCRIPTION`, `MILEAGE_COUNTER_TYPE_ID`) live in `sql_builders/actives.py` and are imported back into the controller — the reverse would close an import cycle. The one exception is `generate_sql_counter_active` in `controllers/design_number_parser/sync_counter_active.py`, where SQL lines are emitted inside a loop that queries the DB row by row.
- Per-operation decisions (what an operation writes, which triggers it works around, user decisions, DB defects) live in `docs/operations/` — **read the page's file before changing one of its operations**, and update it with the change:
  - `parser.md` — `/parser`: creating missing car places (#8), Изменить ТМЦ в модели (#17), Изменить ТМЦ в активе по модели (#18)
  - `ptoir-parser.md` — `/ptoir-parser`: Изменение моточасов, Удалить ПТОиР (#19), Удалить БПТОиР (#21)
  - `design-number-parser.md` — `/design-number-parser`: Изменить (#14), Обновить (#15), Создать (#16) ТМЦ номер
  - `techcard-parser.md` — `/techcard-parser`: Создать техкарты (#12), Наполнить операциями (#13)
  - `mileage-correction.md` — `/mileage-correction`: rollback of a train's mileage with `counter_active_trigger` disabled

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
- Two suites. The default one substitutes Postgres with in-memory SQLite: `ATTACH DATABASE ':memory:' AS public` (+ `StaticPool`) on the `connect` event, since models and raw SQL both target `schema="public"`. It covers validation, plus the shared generate/execute flow in `tests/test_sql_tasks.py`.
- `tests/pg/` runs the generated SQL against a **real copy of grom** and covers what SQLite cannot parse: `ltree`/`::text`, `DO $$` blocks, `nextval`, `FOR UPDATE`, `ON CONFLICT`, `function_get_mileage()` and the PL/Python triggers (`actives_trgger`, `relocate_triger`, `tr_abort_delete`). Target it with `TEST_DB_URL`, or leave it unset to fall back to the `DB_*_MY` set in `.env`; the `pg` marker makes `pytest -m "not pg"` the offline run, and the whole directory skips itself when no database answers.
- Every `tests/pg/` test runs inside a transaction that is always rolled back (`pg_session` binds the session with `join_transaction_mode="create_savepoint"`, so a `commit()` in the code under test only releases a savepoint). Generated SQL goes through `run_generated_sql()`, which hands it to the raw asyncpg connection exactly as the controllers do — a multi-statement `DO $$` body cannot go through a parameterised `execute()`.
- Reference rows (train types, design numbers, storages) are taken from the copy as they are; anything the tests write they create themselves (`tests/pg/factories.py`). The asset move is the exception — it is exercised on a real asset, because `relocate_triger` recomputes the mileage counter from the asset's own history and fails on a synthetic one with an empty counter.
- `car_place.is_delete` defaults to `false` on the working database but still to `true` on the grom copy, so `tests/pg/` compares a created car place's `is_active`/`is_delete` with the catalog's column defaults instead of hardcoding them.

## Linting
- CI (`.github/workflows/ci.yml`, Python 3.12 as in the Docker image) runs `ruff check`, `ruff format --check`, `mypy .` and `pytest -m "not pg"` on every PR and push to `main`; `tests/pg/` is not run there (no grom copy), so run it locally before merging DB-touching changes.
- Dependency versions are pinned in `constraints.txt` (`pip freeze` of the tested venv); CI and `images/Dockerfile` install with `-c constraints.txt`, and so should a local venv. `requirements*.txt` keep only lower bounds — without the constraints every fresh install picked up untested releases (CI got SQLAlchemy 2.1 while everything had been tested on 2.0). To upgrade: `pip install -U -r requirements-dev.txt`, run the full `pytest` (`tests/pg/` included), then `pip freeze > constraints.txt` keeping its two header comment lines.
- SQLAlchemy 2.1: `Row` is typed as a tuple, `Result.tuples()` is deprecated — don't use it.
- Config in `pyproject.toml`. Before committing: `venv/bin/ruff check --fix . && venv/bin/ruff format .` — both must be clean.
- `E501` is off (what the formatter leaves is long strings/SQL); `ASYNC230/240` are off (only small log-file writes after an operation). `Body`/`Parameter`/`Provide` are marked immutable for `B008`.
- `venv/bin/mypy .` must be clean too. `tests.*` has `union-attr` and `arg-type` off (fakes like `FakeRequest`/`owner=None` are deliberate). Narrow `X | None` with a real check, or an `assert` where an invariant guarantees it (e.g. `raw_conn.driver_connection`, ids that the validator reports as errors when missing) — never with `or default`, which changes behaviour.
- Known false positives, suppressed on purpose: `rowcount`/`returns_rows` need `cast(CursorResult[Any], ...)`. Starlette responses in `middleware.py` go through `_send_response` (Starlette's ASGI types vs Litestar's). A comprehension inside a lambda used as a default argument trips mypy's `name-defined` — use a named function.
- `Mapped[...]` annotations in `models.py` must match the column type (`Date` → `dt.date`, `DateTime` → `datetime`); they are only typing, the column type passed to `mapped_column` is what SQLAlchemy uses.
- Bandit is deliberately not used: SQL is built as strings via `sql_escape()` by design, so its SQL-injection check would flag every builder.
- The `ruff format` commit is listed in `.git-blame-ignore-revs`; run `git config blame.ignoreRevsFile .git-blame-ignore-revs` in a fresh clone.

## Known Bugs / Data Issues
- When looking up `CarPlace` by `name`, use `.scalars().all()` and handle 0/1/many explicitly — `.scalar_one_or_none()` raises `MultipleResultsFound` if duplicate names ever reappear in DB

## Agent skills

### Issue tracker

Issues live in GitHub Issues (dimda7/litestar), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.
