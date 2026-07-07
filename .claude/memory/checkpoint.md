# checkpoint.md — Текущий прогресс

## Фаза 1: Базовая структура — ВЫПОЛНЕНА
- [x] Создана папка проекта и venv
- [x] Установлены зависимости
- [x] Созданы файлы контекста (AGENTS, MEMORY, checkpoint, notes)
- [x] Создан app.py с HomeController и AboutController
- [x] Подключён Jinja2 через TemplateConfig + JinjaTemplateEngine
- [x] Подключена статика через StaticFilesConfig
- [x] Созданы шаблоны: base.html, index.html, about.html
- [x] Добавлен Tailwind CDN и кастомный style.css (тёмная тема)
- [x] Исправлен аргумент Template: template_name вместо name (Litestar 2.24)
- [x] Сервер запущен, маршруты / и /about работают (200 OK)
- [x] Подключить postgres через advanced_alchemy (192.168.92.143\merged_db:6432 user:password postgres:postgres)
- [x] Сделать аутенфикацию в отдельной странице на основе таблицы users из бд где поле usrname - логин, а поле password пароль от хэш функции bcrypt
- [x] Сделать отдельную страницу для парсинга файл excel. Файл выбирается по кнопке в файловой системе

## Фаза 2: Генерация SQL из Excel — ВЫПОЛНЕНА
- [x] Кнопка "Создать SQL файл" после парсинга Excel
- [x] Добавлены ORM-модели: TrainType, CarPlace, DesignNumber, Models (схема grom)
- [x] Замена raw SQL на advanced_alchemy ORM-запросы (select() через db_session)
- [x] FK-валидация: train_type, car_place, design_number должны существовать в БД
- [x] UNIQUE-валидация (id_train_type, lcn, id_car_place, id_design_number, is_default)
- [x] UNIQUE-валидация (lcn, car_place) WHERE is_default=true
- [x] UNIQUE-валидация (id_car_place, id_train_type, id_design_number) WHERE is_default=true
- [x] Проверка конфликтов внутри загружаемой пачки (entre rows)
- [x] Подсветка проблемных строк красным + список ошибок над таблицей
- [x] JSON API: {status: "ok", sql: "..."} или {status: "error", errors: [...]}
- [x] Фронтенд: fetch() вместо form submit, Blob для скачивания .sql файла
- [x] Исправлено: is_default генерируется как TRUE/FALSE (не 0/1)
- [x] Исправлено: столбец lcn (не lsn) — соответствует БД

## Фаза 3: Редизайн интерфейса — ВЫПОЛНЕНА
- [x] Переход на светлую тему (фон #f5f5f5)
- [x] Шрифт Roboto (Google Fonts)
- [x] Белые карточки с border-radius: 16px
- [x] Боковое меню навигации (sidebar) с иконками
- [x] Material Design стиль кнопок и полей ввода
- [x] Обновлены все шаблоны: base.html, login.html, index.html, about.html, users.html, parser.html
- [x] Добавлен active_page в контекст для подсветки активного пункта меню

## Фаза 4: Улучшения интерфейса и стабильность — ВЫПОЛНЕНА
- [x] Пагинация на странице Парсинг Excel (как на странице Пользователи)
- [x] Выбор количества записей на странице (10, 25, 50, 100)
- [x] Нумерация строк с учётом текущей страницы
- [x] PRG-паттерн (Post-Redirect-Get) для загрузки файла — исправлен 405 Method Not Allowed
- [x] Хранение данных парсинга в JSON-файлах (parser_data/) вместо cookie-сессии — исправлен ERR_RESPONSE_HEADERS_TOO_BIG
- [x] Автоочистка файлов старше 1 часа
- [x] Иконка пользователя перед именем в шапке
- [x] Текущая дата и время (обновляется каждую секунду)
- [x] По умолчанию 10 записей на странице

## Фаза 5: Парсинг ТМЦ (design_number) — ВЫПОЛНЕНА
- [x] Переименована страница "Парсинг Excel" → "Парсинг моделей" (sidebar, title, heading)
- [x] Создан новый контроллер `controllers/design_number_parser.py` (путь `/design-number-parser`)
- [x] Создан шаблон `templates/design_number_parser.html`
- [x] Добавлена ORM-модель `CounterGroup` (таблица `public.counter_group`)
- [x] Обновлена ORM-модель `DesignNumber`: добавлены `id_counter_group` и `is_serial_1c`
- [x] Добавлен msgspec-схема `DesignNumberSelectSheetRequest` в `schemas.py`
- [x] Две кнопки: "Обновить id_counter_group" и "Обновить is_serial_1c"
- [x] Для каждой кнопки: generate-sql (скачать .sql) + execute (атомарно в БД)
- [x] Валидация: `number` → `design_number.number`, `counter_group` → `counter_group.name` → `counter_group.id`
- [x] Пункт меню "Парсинг ТМЦ" в sidebar
- [x] Исправлен select-sheet: заменен `dict` на msgspec Struct для URL-encoded данных
- [x] Исправлен 404: заменён импорт старого `controllers/design_number` на `controllers.design_number_parser`
- [x] Удалены старые файлы `controllers/design_number.py` и `templates/design_number.html`

## Фаза 6: Аудит проекта и исправление критичных проблем — ВЫПОЛНЕНА
- [x] SESSION_SECRET больше не генерируется заново при каждом запуске (`controllers/auth.py`) — раньше это сбрасывало все сессии на рестарте и ломалось при нескольких воркерах
- [x] Добавлено поле `session_secret` в `config.py` (Settings), читается из `.env` как hex-строка
- [x] `app.py` использует `settings.session_secret` для `CookieBackendConfig`
- [x] В `.env` и `.env.example` добавлена переменная `SESSION_SECRET` (в example — с инструкцией по генерации)
- [x] Экранирование пользовательских строк в генерируемом SQL — добавлен `sql_utils.py` с `sql_escape()`
- [x] `sql_escape()` применён в `parser.py`, `train_parser.py`, `design_number_parser.py` во всех местах, где строки из Excel (lcn, train_name, active_number, lcn_new, serial_number, number и др.) подставлялись в SQL/лог напрямую через f-строку (уязвимость к разрыву SQL / инъекции в сгенерированном .sql-файле)
- [x] `cryptography>=42.0` добавлена в `requirements.txt` явно (требуется для `CookieBackendConfig`, раньше отсутствовала в списке зависимостей)
- [x] Кастомные страницы ошибок 404/500 (`templates/errors/404.html`, `templates/errors/500.html`) — простой layout без сайдбара, переиспользует стили `.login-page`/`.login-card`/`.btn`
- [x] В `app.py` зарегистрированы `exception_handlers` (404, 500); обработчики должны быть синхронными `def`, а не `async def` — Litestar вызывает их без `await`
- [x] 500-обработчик логирует полный трейсбек через `logging` (traceback не уходит в ответ пользователю), добавлен минимальный `logging.basicConfig` в `app.py` — полноценная настройка логирования (файлы, ротация) остаётся в следующих шагах
- [x] Убраны из git закоммиченные лог-файлы (`git rm --cached log/*.log`) — файлы остались на диске, но больше не отслеживаются; `log/` уже был в `.gitignore`, просто не подхватывал задним числом уже добавленные файлы
- [x] Убрано дублирование `_load_data`/`_save_data`/`_cleanup_old_files` — вынесено в новый модуль `parser_storage.py` (+ `PARSER_DATA_DIR`, `LOG_DIR`). В `parser.py`, `train_parser.py`, `design_number_parser.py` функции импортируются с алиасами (`load_data as _load_data` и т.д.), чтобы не трогать десятки call site'ов внутри контроллеров. Замечено: `train_parser.py` никогда не вызывал `_cleanup_old_files` (в отличие от двух других) — поведение сохранено как было, это не тронуто

## Баг: "Ошибка запроса: SyntaxError: Unexpected token '<'" на странице Парсинг моделей — ИСПРАВЛЕНО
- Причина: в БД таблица `car_place` содержит 443 неуникальных `name` (одно имя — до 22 разных `id`). `_validate_and_build_rows` (`parser.py`) делал `select(CarPlace.id).where(CarPlace.name == position).scalar_one_or_none()`, который кидает `sqlalchemy.exc.MultipleResultsFound`, если найдено больше одной строки. Воспроизведено на реальном файле `Редактирование моделей_Тяги торсиона_Ласточки, Финисты, Сапсаны.xlsx` (лист «Добавить в модели», 16 из 116 уникальных `position` попадают на дубликаты)
- До кастомных страниц ошибок (Фаза 6) это давало сломанный, но валидный JSON по умолчанию от Litestar; после добавления HTML-страницы 500 — фронтенд получал `<!DOCTYPE ...>` и падал на `JSON.parse`
- [x] `parser.py`: замена `.scalar_one_or_none()` на `.scalars().all()` для `CarPlace` — 0 совпадений → прежняя ошибка «не найден», >1 → новая ошибка «car_place неоднозначен» с списком конфликтующих `id` (по аналогии с уже существующим паттерном row-level ошибок), вместо падения всего запроса
- [x] `parser.py`: `generate-sql` и `execute-sql` обёрнуты в try/except вокруг `_validate_and_build_rows` — любая будущая неожиданная ошибка БД вернётся как JSON `{"status": "error", ...}`, а не улетит в общий HTML-обработчик 500 (паттерн уже был в `execute_sql`/`execute_delete` и в `train_parser.py`, теперь единообразно везде)
- Не исправлено (требует решения на уровне данных, не кода): почему `car_place.name` не уникален в БД — 443 дублирующихся имени всего, это существующая проблема данных, а не баг парсера

## Фаза 7: CSRF-защита — ВЫПОЛНЕНА
- Подключён `CSRFConfig` (`litestar.config.csrf`) в `app.py` — до этого формы и AJAX-запросы (`/auth/login`, `/parser/execute-sql`, `/parser/delete-rows`, `/train-parser/execute`, `/design-number-parser/update-*` и др.) были уязвимы к CSRF: авторизованная сессия давала возможность чужому сайту отправить запрос от имени залогиненного пользователя
- Секрет переиспользован из `settings.session_secret` (`.hex()`) — новая переменная в `.env` не потребовалась; используется для HMAC-подписи токена, а не для шифрования сессии (другое криптографическое назначение, но тот же случайный секрет)
- `templates/base.html`: токен рендерится в `<meta name="csrf-token">` + добавлен JS-хелпер `appendCsrfToken(formData)`, доступный во всех шаблонах, унаследованных от `base.html`
- `templates/login.html` не наследует `base.html` (свой `<head>`) — токен добавлен туда отдельным скрытым полем `_csrf_token`
- Обычные `<form method="post">` (`parser.html`, `train_parser.html`, `design_number_parser.html` — upload и select-sheet) получили скрытое поле `<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">`
- AJAX-запросы через `fetch()` с `FormData` (generate-sql, execute-sql, delete-rows, execute-delete и аналогичные в design-number-parser/train-parser) — токен добавляется через `appendCsrfToken()` перед отправкой
- Проверено вживую через `curl`: GET `/auth/login` ставит куку `csrftoken` и рендерит совпадающий токен в форме; POST без токена → `403 Forbidden`; POST с валидным токеном — доходит до контроллера
- Попутно удалён неиспользуемый `.venv/` (126MB, неполный набор зависимостей — отсутствовал `jinja2`). Рабочий интерпретатор — `venv/`, PyCharm использует отдельно настроенный SDK "Python 3.10 (litestar) (3)", `.iml` лишь исключает обе папки из индексации

## Фаза 8: Логирование по модулям с ротацией — ВЫПОЛНЕНА
- Создан `logging_config.py` с `configure_logging(level)` на базе `logging.config.dictConfig`
- Для каждого логгера из `MODULE_LOGGERS` (`app`, `parser`, `train_parser`, `design_number_parser` — имена уже использовались через `logging.getLogger(...)` в контроллерах, но раньше писали только в консоль через общий `basicConfig`) настроен свой `RotatingFileHandler`: `log/<module>.log`, ротация при 5MB, 5 бэкапов, плюс дублирование в консоль (`propagate=False`, чтобы сообщения не удваивались через root)
- `app.py`: `logging.basicConfig(...)` заменён на `configure_logging(level=settings.log_level)`
- `config.py`: добавлено поле `log_level` (читается из `.env` как `LOG_LEVEL`, по умолчанию `INFO`) — по правилу «не хардкодить» из `AGENTS.md`
- `.env.example`: добавлена переменная `LOG_LEVEL` с комментарием
- Не тронуто: отдельные per-операционные аудит-файлы (`log/insert_models_*.log`, `log/update_counter_group_*.log` и т.п.) — это не логи через модуль `logging`, а ручная запись SQL-аудита каждой операции execute-sql/execute/update прямо в контроллерах (`open(log_file, "a")`); осталось как есть, т.к. это другая сущность (аудит конкретной операции с уникальным именем файла, а не поточный лог модуля)
- Проверено вживую: `logging.getLogger("parser").info(...)` и аналоги для трёх других логгеров пишутся и в консоль, и в свой `log/<module>.log`

## Фаза 9: Тесты FK/UNIQUE-валидации парсинга — ВЫПОЛНЕНА
- Добавлены `pytest`, `pytest-asyncio`, `aiosqlite` в `requirements-dev.txt` (наследует `requirements.txt`); `pytest.ini` — `asyncio_mode = auto`, `pythonpath = .`
- `tests/conftest.py`: фикстура `db_session` — in-memory SQLite вместо реального Postgres. Все модели (`schema="public"`) и часть контроллеров (`train_parser.py`) обращаются к таблицам как `public.<table>` через сырой SQL — в SQLite это решено через `ATTACH DATABASE ':memory:' AS public` на событии `connect` (со `StaticPool`, чтобы in-memory БД не терялась между «соединениями»). Так и ORM `select()`, и `text("... FROM public.models")` бьют в одни и те же таблицы без расхождения с продакшен-кодом
- `tests/test_parser_validation.py` (14 тестов) — `ParserController._validate_and_build_rows`: FK не найден (train_type/car_place/design_number), **регрессия на баг с неуникальным `car_place.name`** (MultipleResultsFound), дубликат строки против существующей записи в БД и внутри пачки, оба UNIQUE-ограничения (`lcn+car_place` и `car_place+train_type+design_number` при `is_default=true`) — конфликт с БД и конфликт внутри пачки, отдельно проверено что не-default строки эти ограничения не проверяют, fallback `lsn`→`lcn`
- `tests/test_design_number_parser_validation.py` (15 тестов) — `_validate_counter_group` и `_validate_is_serial_1c`: пустые поля, FK не найден, регистронезависимое сопоставление `counter_group.name`, весь набор допустимых значений `is_serial_1c` (true/false/1/0/да/нет) и невалидное значение
- `tests/test_train_parser_helpers.py` (11 тестов) — чистые функции разбора LSN (`_lcn_to_model`, `_lcn_to_lcn`, `_lcn_to_prelcn`, `_parse_car_number`), без обращения к БД
- `tests/test_train_parser_validation.py` (4 теста) — только ветки `_validate_train_rows`, не доходящие до `text("... WHERE lcn::text = :lcn")`: пустые `lsn`/`itemnum`, design_number не найден. **Не покрыто намеренно**: happy-path с реальным разрешением `car_place_id`/`id_actives_parent` — использует Postgres-специфичный оператор `::text`, который SQLite не парсит; для полного покрытия нужен настоящий Postgres в тестах (например, testcontainers — не доступен в этом окружении: `docker` есть, но без прав на сокет)
- Итого 43 теста, `venv/bin/pytest` — все зелёные

## Фаза 10: cleanup_old_files() в train_parser.py — ВЫПОЛНЕНА
- `controllers/train_parser.py`: добавлен импорт `cleanup_old_files as _cleanup_old_files` из `parser_storage` и вызов в начале `try` в `/upload`, тем же паттерном, что уже был в `parser.py` и `design_number_parser.py` — раньше это был единственный из трёх парсеров, где JSON-файлы сессии в `parser_data/` не удалялись автоматически по истечении часа

## Фаза 11: Реальная отмена SQL-запроса в SQL-консоли — ВЫПОЛНЕНА
- Баг: на странице «Выполнить SQL скрипт» кнопка отмены (красный квадрат) не прерывала запрос — таймер шёл до конца, данные всё равно возвращались
- Причина: `DB_PORT=6432` — это pgbouncer. Старая реализация (`db_manager.cancel_backend`) слала `SELECT pg_cancel_backend(pid)` через **новое** соединение из того же пула. Пока единственное серверное соединение pgbouncer занято долгим запросом, новый SQL-запрос на отмену встаёт в очередь и выполняется только после того, как исходный запрос сам закончится — то есть отмена приходит слишком поздно и ничего не даёт
- Исправлено (`controllers/sql_console.py`): выполнение statements обёрнуто в `asyncio.Task` (`_running_tasks: dict[str, asyncio.Task]`), `/sql-console/cancel` вызывает `task.cancel()` вместо отдельного SQL-запроса. asyncpg (проверено, v0.31.0, `Connection._cancel`) при получении `CancelledError` во время ожидания ответа сервера сам открывает отдельный сырой TCP-сокет и шлёт нативный Postgres `CancelRequest` (backend_pid + secret) — это pgbouncer обрабатывает напрямую, без резервирования слота из пула
- Удалена мёртвая функция `db_manager.cancel_backend()` и связанный `import asyncio` (последний потребитель — старый код в `sql_console.py`)
- Проверено вживую через `litestar.testing.TestClient` (сессия подставлена напрямую через `set_session_data`, минуя логин): `select pg_sleep(3), 1 from generate_series(1,10)` (10 строк × 3с = 30с суммарно) + отмена через ~1.5с → `execute` вернул `{"status": "error", "message": "Запрос прерван пользователем"}` за ~1.52с вместо ожидания все 30с
- 43/43 теста зелёные (regressions не внесены)

## Следующие шаги
1. Разобраться с дублирующимися `car_place.name` в БД (443 группы дублей) — сейчас такие строки Excel просто помечаются как ошибка валидации и не обрабатываются
2. Покрыть happy-path `train_parser._validate_train_rows` (разрешение `car_place_id`/`id_actives_parent`) — нужен реальный Postgres в тестовом окружении (testcontainers или аналог), см. Фазу 9
3. Rate-limiting / защита от брутфорса на `/auth/login` — отмечалось в общем аудите проекта, пока не реализовано
