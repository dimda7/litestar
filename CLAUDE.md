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
- Generated SQL text lives in `sql_builders/` (`actives`, `models`, `car_place`, `train`, `ptoir`, `orders`, `design_number`, `mileage`), never in the controllers: the same builder feeds both "Скачать SQL-файл" and "Выполнить в базе данных" (plus the `log/*.log` echo), so the downloaded file and the executed statements cannot drift apart. Controllers import them as `from sql_builders import actives as actives_sql`. Constants the SQL bakes in (`ACTIVE_NUMBER_LENGTH`, `ACTIVE_NUMBER_COUNTER_DESCRIPTION`, `MILEAGE_COUNTER_TYPE_ID`) live in `sql_builders/actives.py` and are imported back into the controller — the reverse would close an import cycle. The one exception is `design_number_parser.generate_sql_counter_group`, where SQL lines are emitted inside a loop that queries the DB row by row.
- "Корректировка введенного пробега" (`controllers/mileage_correction.py`) rolls a train's mileage back to the state of the last manually-entered reading before a selected one, and resets the counter — **with `counter_active_trigger` disabled**, because that trigger's PL/Python body (`counter_active_python`) is what generates the `mileage_train` rows in the first place: with it on, the `UPDATE` regenerates what the `DELETE` just removed. The counter row is addressed by `id_active = train.active AND id_counter_type = 3 AND is_train = true`, never by `counter_active.id`.
  - The trigger fills **every calendar day** of a gap between two manual readings, and only the day matching the newly-entered reading gets a non-null `date` — the interior days get `date IS NULL` and never appear in the page's list (filtered on `date is not null`). `resolve_correction` therefore rolls back to the last row with a non-null `date` before the selected one (never the closest row by `date_average` alone, which could be one of those hidden interior rows — see `test_null_date_row_is_not_picked_as_the_previous_state`), and the generated `DELETE` uses `date_average > <that kept row's date_average>` (strictly, against the *kept* row), not `>=` against the selected row's own date — otherwise a hidden interior row between the kept and the selected one survives the delete untouched. The counter's new `value` **and** `date` both come from that same kept row (never the selected one) — `SET value, date` restores the counter to exactly that row's own state, not a mix of one row's value and another's timestamp.
  - Two schema facts shape the tests: a UNIQUE index on `counter_active (id_active, id_counter_type)` makes the "several counters" branch unreachable in production (defensive only, covered on SQLite), and `actives_trgger` creates the `counter_active` rows itself on asset INSERT with `is_train = false` — so `tests/pg/` adopts the trigger-made row instead of inserting one, in a single `UPDATE` that `counter_active_trigger`'s `WHEN` (which reads `OLD`) does not fire. The downloadable `BEGIN; … COMMIT;` variant is never run in `tests/pg/`: its `COMMIT` would end the fixture's own transaction and commit the test data.
- "Изменение моточасов" on `/ptoir-parser` (`validate_change_hours_rows`, `sql_builders/ptoir.change_hours`) overwrites `counter_active.value` of the ПТОиР's asset (`id_active = ptoir.id_active AND id_counter_type = 1`; `ptoir` itself has no `value` column) with the file's `значение` × 10 — the file holds hours with at most one decimal, the column holds integer tenths. The updates run under `SET session_replication_role = replica`, because `counter_active_hours_jump` (PL/Python, BEFORE UPDATE) takes a written `value` as a source reading and **adds** it to the old total instead of replacing it. Both `SET`s and the updates share one transaction: a plain `SET` is rolled back with it, so a failed run cannot return the pgbouncer connection to the pool with every trigger (FKs included) still off. `value_source` and `date` are deliberately left alone — a user decision; the next regular reading is therefore still counted from the old `value_source`. The overwrite is irreversible, so `log/ptoir_hours_<timestamp>.log` records each asset's old `value`/`value_source`.
- Connection URLs are built by `db_profiles.build_url()`, which percent-encodes user and password — a typed password like `p@ss` otherwise makes SQLAlchemy parse the host as `ss@…`.
- "Изменить okz в модели" and "Добавить строку в модель" **create** a car place the file names but `car_place` lacks, instead of reporting "car_place не найден" (`sql_builders/car_place.py`, issue #8). A validated row refers to its car place by id (`int`, it exists) or by name (`str`, it has to be created). `insert_missing()` emits one `INSERT … (name, car_number) … ON CONFLICT (name) DO NOTHING RETURNING name` per name, before the model statements; `car_number` comes from the `_(NN)` suffix via `parse_car_number()` (shared with `train_parser`), every other column is left to its default. The model statements reference a new car place through `(SELECT id … WHERE name = …)`: its id is unknown when the file is generated, and `ON CONFLICT` keeps the file runnable if someone creates the name before it runs. Both execute paths run the builder's own lines through `controllers/parser/common.execute_sql_lines()`, which collects the `RETURNING` rows — so the message and log list only the names that run actually created — and uses `exec_driver_sql`, not `text()`: a typed name is inlined into the SQL, and `text()` reads a `:1` after a space or bracket as a bind parameter. "Изменить okz в активе по модели" still reports a missing car place as an error.

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
- Two suites. The default one substitutes Postgres with in-memory SQLite: `ATTACH DATABASE ':memory:' AS public` (+ `StaticPool`) on the `connect` event, since models and raw SQL both target `schema="public"`. It covers validation only.
- `tests/pg/` runs the generated SQL against a **real copy of grom** and covers what SQLite cannot parse: `ltree`/`::text`, `DO $$` blocks, `nextval`, `FOR UPDATE`, `ON CONFLICT`, `function_get_mileage()` and the PL/Python triggers (`actives_trgger`, `relocate_triger`, `tr_abort_delete`). Target it with `TEST_DB_URL`, or leave it unset to fall back to the `DB_*_MY` set in `.env`; the `pg` marker makes `pytest -m "not pg"` the offline run, and the whole directory skips itself when no database answers.
- Every `tests/pg/` test runs inside a transaction that is always rolled back (`pg_session` binds the session with `join_transaction_mode="create_savepoint"`, so a `commit()` in the code under test only releases a savepoint). Generated SQL goes through `run_generated_sql()`, which hands it to the raw asyncpg connection exactly as the controllers do — a multi-statement `DO $$` body cannot go through a parameterised `execute()`.
- Reference rows (train types, design numbers, storages) are taken from the copy as they are; anything the tests write they create themselves (`tests/pg/factories.py`). The asset move is the exception — it is exercised on a real asset, because `relocate_triger` recomputes the mileage counter from the asset's own history and fails on a synthetic one with an empty counter.
- `car_place.is_delete` defaults to `false` on the working database but still to `true` on the grom copy, so `tests/pg/` compares a created car place's `is_active`/`is_delete` with the catalog's column defaults instead of hardcoding them.

## Known Bugs / Data Issues
- When looking up `CarPlace` by `name`, use `.scalars().all()` and handle 0/1/many explicitly — `.scalar_one_or_none()` raises `MultipleResultsFound` if duplicate names ever reappear in DB

## Agent skills

### Issue tracker

Issues live in GitHub Issues (dimda7/litestar), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.
