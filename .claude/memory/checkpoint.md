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

## Фаза 12: Парсинг активов — новая колонка ТМЦ, прогресс, пересчёт пробега — ВЫПОЛНЕНА
- [x] «Изменение ТМЦ номера»: колонка `Новый ТМЦ номер` принимается как альтернатива `Новая Позиция ТМЦ` (`_validate_design_number`, паттерн как у `Новый с/н`/`Новый Серийный номер`); старые файлы работают как раньше
- [x] «Изменение ТМЦ номера» переведено на фоновые задачи с прогрессом (как create-actives/ПТОиР): `/design-number/generate-sql/start` и `/design-number/execute/start` вместо старых `/generate-sql-design-number` и `/update-design-number`; строки берутся из серверного хранилища сессии (`parser_data/`), а не пересылаются из браузера
- [x] Эндпоинт прогресса стал общим для всех флоу страницы: `/actives-parser/progress/{task_id}` (бывший `/create-actives/progress/...`)
- [x] Новая кнопка «Пересчитать пробег» (файл с колонкой `Актив`) — замена peewee-функций `update_milage_start` + `recount_counter` + `update_milage_start_const` через advanced_alchemy:
  - добавлены ORM-модели `Relocate`, `MileageStart` (структура снята с реальной БД; у `mileage_start.id` есть sequence-default)
  - `_validate_recount_mileage`: поправка total по истории relocate до отсечки 13.05.2022 (`MILEAGE_RECOUNT_CUTOFF`, +3ч `MILEAGE_TZ_SHIFT`): склад→поезд прибавляет `SUM(mileage_train.mileage_average)` за период от перемещения до отсечки, поезд→склад вычитает; поезд→поезд и склад→склад игнорируются
  - порядок SQL обязателен (одна транзакция): 1) `milage_const` из файла (UPDATE, либо INSERT при отсутствии записи mileage_start), 2) `milage = COALESCE(milage_const, 0) + (total)` — читает milage_const в момент выполнения, 3) `counter_active.value` через `function_get_mileage(id_active, date::date)` прямо в UPDATE — считается Postgres'ом после обновления mileage_start; скачанный .sql-файл не расходится с БД
  - необязательная колонка `milage_const`: заголовок матчится без регистра/краевых пробелов (включая `\xa0`), оба написания milage_const/mileage_const — реальный файл содержал `mileage_const\xa0`, из-за точного сравнения const-блок молча пропускался (исправлено); 0 и пустые значения игнорируются как в старом скрипте; при отсутствии записи mileage_start с заданным const — INSERT, без const — ошибка валидации
  - поезда (`id_unit_type=1`): mileage_start обновляется, счётчик не трогается (как раньше); «Актив не найден», дубликаты, >1 записи mileage_start/счётчика — ошибки валидации с номером строки (раньше молчаливый print)
  - эквивалентность старой логике проверена эмпирически: дословный peewee-алгоритм (со строковыми сравнениями дат) выполнен на реальной БД — 18/18 totals совпали с новой реализацией
- [x] Тесты: новый `tests/test_actives_parser_validation.py` (17 тестов: обе колонки ТМЦ, все ветки recount-валидации, порядок SQL-блоков, заголовок с `\xa0`), итого 68 тестов зелёные
- [x] Живые smoke через `litestar.testing.TestClient` (сессия через `set_session_data`, CSRF из куки `csrftoken` заголовком `x-csrftoken`, БД-профиль через `db_manager.set_active_profile`) — генерация SQL на реальной БД `grom-tk`, запись только в откатываемых транзакциях
- Коммиты: c005398 (recount mileage + прогресс + новая колонка), a2197a9 (milage_const)

## Фаза 13: Парсинг активов — именные активы, прогресс serial_number, колонка active_number — ВЫПОЛНЕНА
- [x] Новая кнопка «Создать именной актив» (файл: `Актив`, `ТМЦ`, `Положение`, `Партия`) — как «Создать активы из ТМЦ», но active_number задан в файле:
  - `_validate_create_named_actives`: актив НЕ должен существовать в БД (обратная проверка), дубликаты в файле — ошибка, `ТМЦ`→`design_number.number`, `Положение`→`storage.name`, `Партия`→`consignment.name` (с кэшами); все ошибки по строкам
  - `_build_create_named_actives_sql_body`: DO-блок как в create-actives (nextval для location/actives, `storage.last_lcn` FOR UPDATE + инкремент + UPDATE в конце), но `active_number` — литерал из файла, `iterator_number_last` не используется; на актив своя запись location (`id_type_location=1`) и `lcn = 'S<storage>.<n>'::ltree`
  - эндпоинты `/create-named-actives/{generate-sql,execute}/start`, аудит-лог `create_named_actives_*.log`; DO-блок проверен на реальной БД в откатанной транзакции (актив создался с lcn S6.26892, после rollback отсутствует)
- [x] «Пересчитать пробег»: колонка актива — `Актив` или `active_number` (без регистра/краевых пробелов, тем же механизмом, что milage_const)
- [x] «Изменить серийные номера» переведено на фоновые задачи с прогрессом: `/serial-number/{generate-sql,execute}/start` вместо `/generate-sql-serial-number` и `/update-serial-number`; `_validate_serial_number` с progress; строки из серверного хранилища сессии — все 5 флоу страницы теперь единообразны
- [x] Удалён мёртвый код шаблона: `buildFormData()` и `ALL_ROWS` (сериализация всех строк файла в HTML раздувала страницу на больших файлах)
- [x] Тесты: 75 зелёных (+6 именные активы, +1 колонка active_number)
- Коммиты: 84952b1 (именные активы); active_number + serial-прогресс — в рабочем дереве на момент записи

## Фаза 14: Парсинг активов — удаление активов — ВЫПОЛНЕНА
- [x] Кнопка «Удалить активы» (btn-danger, добавлен в style.css) — файл с колонкой `Актив`/`active_number`, флоу с прогрессом как у остальных, эндпоинты `/delete-actives/{generate-sql,execute}/start`, аудит-лог `delete_actives_*.log`, confirm «Действие необратимо»
- [x] Найденные особенности БД (проверено на grom-tk, applies везде):
  - триггер `actives_trgger` (AFTER INSERT на actives, plpython) создаёт строку counter_active каждому активу — блокировать удаление по счётчику нельзя, он есть у всех
  - DELETE из counter_active запрещён DBA-триггером `tr_abort_delete` (dba.fn_abort_delete, безусловный RAISE) — SQL обрамляется `ALTER TABLE counter_active DISABLE/ENABLE TRIGGER tr_abort_delete` в одной транзакции (нужны права владельца; приложение ходит под postgres)
- [x] Семантика «строгого» удаления (выбор пользователя + 2 итерации расширения):
  - удаляются вместе с активом: пустые заказы (по id_active и через ПТОиР по orders.id_ptoir; до ptoir из-за FK), ptoir_level_warning + ptoir, counter_active, mileage_start, сам актив, его location (только если на неё не ссылаются другие actives/materials/relocate — NOT EXISTS в момент выполнения)
  - блокируют (ошибка по строке): relocate (id_active и id_root_active), order_to_actives, active_additional_field, actives_to_main_ptoir, materials_to_actives, mileage_history_actives — список DELETE_ACTIVES_BLOCKERS; заказ со связанными записями хотя бы в одной из 20 таблиц ORDERS_DEPENDENCY_CHECKS (labor_costs, orders_to_specification, material_1c, relocate.id_order и т.д. — снято с information_schema)
  - проверки батчевые: один запрос на таблицу для всех активов/заказов файла (bindparam expanding для сырых SELECT)
- [x] Добавлены модели: Orders (id_ptoir), OrderToActives, ActiveAdditionalField, ActivesToMainPtoir, MaterialsToActives, MileageHistoryActives; в Relocate — id_order, id_root_active
- [x] Тесты: 81 зелёный; для SQLite зависимые таблицы заказов создаются DDL-хелпером `make_order_dependency_tables` из ORDERS_DEPENDENCY_CHECKS
- [x] Проверено: полный цикл удаления (актив+ПТОиР+пустой заказ) на grom-tk в откатанной транзакции; валидация реального файла (1283 актива) на grom-prod read-only — 0 ошибок
- Замечено: приложение реально используется с профилем grom-prod (активы файла и их заказы есть только там)

## Фаза 15: Создание активов из ТМЦ — колонка АРТИКУЛ и Excel-отчёт — ВЫПОЛНЕНА
- [x] Колонка ТМЦ: новое имя `АРТИКУЛ` как альтернатива старому `Номер ТМЦ (DU,KP,A2V)` (без регистра/краевых пробелов, общий паттерн поиска колонок); отсутствие обеих — явная ошибка валидации вместо молчаливого пропуска всех строк
- [x] После успешного «Выполнить в базе данных» скачивается `actives.xlsx` (active_number, serial_number):
  - номера восстанавливаются детерминированно из счётчика: итоговое `iterator_number_last.number` читается в той же транзакции, что и DO-блок (FOR UPDATE ещё держит строку — параллельный запуск не вклинится), `_reconstruct_created_active_numbers` раскладывает по порядку valid_rows включая раскрытие «Количество»
  - серийники — обратным SELECT из БД по созданным номерам (отчёт отражает реально записанное, для количеств >1 — 'none')
  - xlsx собирается в памяти (`_build_created_actives_xlsx`, openpyxl → base64 в ответе прогресса), фронтенд декодирует atob → Blob → download; созданные номера пишутся и в аудит-лог create_actives_*.log
- [x] Тесты: 86 зелёных (+3 колонка АРТИКУЛ — валидатор create-actives впервые покрыт, +2 восстановление номеров и xlsx)
- [x] Проверено на grom-tk в откатанной транзакции: восстановленные номера совпали с фактически созданными 1:1 (включая строку с количеством 2)

## Фаза 16: Страница «Иерархия активов» (id_actves_parent / id_actives_root) — ВЫПОЛНЕНА
- [x] Новый контроллер `controllers/active_hierarchy.py` (`/active-hierarchy`), шаблон `templates/active_hierarchy.html`, зарегистрирован в `app.py`, пункт меню в `base.html`
- [x] Две независимые карточки на одной странице — «Верхний актив» (`id_actves_parent`, родитель на уровень выше по lcn: `subltree(act.lcn, 0, nlevel(act.lcn) - 1)`) и «Головной актив» (`id_actives_root`, вершина дерева: `subltree(act.lcn, 0, 1)`), обе через общую конфигурацию `HIERARCHY_KINDS` — SET-колонка берётся из конфига, а не хардкодится, что исключает копи-паст баг из исходного запроса пользователя (там для Головного актива по ошибке было `SET id_actves_parent` вместо `id_actives_root`)
- [x] Кнопка «Показать, что обновлять» — POST `/active-hierarchy/preview/{parent|root}`, SELECT со сравнением текущего/нового значения; кнопка «Обновить» — POST `/active-hierarchy/execute/{parent|root}`, тот же UPDATE, что и в preview-условии; аудит-лог `log/update_active_hierarchy_{kind}_*.log`
- [x] Пагинация результата на клиенте: «Всего строк: N» + разбивка по 50 строк на страницу (переиспользованы классы `.pagination`/`.page-btn` из `style.css`, до этого использовались только для серверной пагинации на страницах Пользователи/Парсинг Excel) — для обеих кнопок «Показать, что обновлять»
- [x] Проверено на реальной БД `grom-tk`: preview и execute дают одинаковое число строк (564 для Верхний актив, 11 для Головной актив); UPDATE прогнан в отдельной транзакции с явным `rollback()` — после отката расхождения (564) остались нетронутыми, реальных изменений не было
- [x] Логика клиентской пагинации проверена изолированно в Node (эмуляция DOM): 564→12 страниц (последняя — 14 строк), 11→1 страница без контролов, 0→только alert, 50→1 страница, 51→2 страницы
- Коммиты: cdc2268 (страница + preview/execute), 7eeed56 (пагинация 50/страница)

## Фаза 17: Парсинг моделей — кнопка "set serial='none' lcn" с прогрессом — ВЫПОЛНЕНА
- [x] Новая кнопка на странице «Парсинг моделей» (`controllers/parser.py`, `templates/parser.html`): по модельному lcn вида `M9.6.5` (`9` — id_train_type, `6.5` — остаток пути после первой точки) находит все поезда этого типа (`public.train`) и подставляет их id вместо `M9`, получая список lcn активов; `UPDATE public.actives SET serial_number = 'none' WHERE lcn::text IN (...)`
- [x] Колонка `lsn`/`lcn` ищется без учёта регистра (общий паттерн fuzzy-заголовков проекта), `_parse_model_lcn` — регуляркой `^\D*(\d+)(?:\.(.*))?$`
- [x] «Скачать SQL-файл» и «Выполнить в базе данных» переведены на фоновые задачи с прогресс-баром — по аналогии со страницей «Парсинг ПТОиР» (оверлей со спиннером, опрос `/parser/progress/{task_id}`, эндпоинты `/parser/serial-none/{generate-sql,execute-sql}/start`); до этого страница «Парсинг моделей» не имела прогресс-инфраструктуры вообще — добавлена с нуля (`_progress`/`_tasks`/`_cleanup_progress`, как в `train_parser.py`/`ptoir_parser.py`)
- [x] Строки Excel с одинаковым (или пересекающимся) lsn объединяются в **один** `UPDATE` на весь файл вместо повторяющихся идентичных запросов на каждую строку (`_merge_serial_none_lcns` — дедуп по всему списку lcn_trains, общий для generate и execute)
- [x] Добавлен хелпер `make_train` в `tests/conftest.py`; 14 новых тестов (`tests/test_parser_serial_none_validation.py`): парсинг lcn (с префиксом/без, с путём/без), алиас колонки lsn/lcn, кэш поездов по типу, отсутствие поездов/колонки, слияние дублей/пересечений в один запрос — итого 100 тестов зелёных
- [x] Проверено на реальном файле пользователя `Редактирование моделей межвагонный переход Сапсан1.xlsx` (лист «Удалить из моделей», grom-tk): 8 строк с 4 повторяющимися парами lsn (`M9.6.5`, `M10.6.12`, `M11.6.5`, `M54.6.5`) → 1 `UPDATE` на 19 уникальных lcn вместо 8 отдельных; в откатанной транзакции `rowcount = 19`
- Коммиты: см. следующий коммит после этой записи

## Фаза 18: Парсинг моделей — кнопка "Переместить активы" (аналог move_active) — ВЫПОЛНЕНА
- [x] Новая кнопка на странице «Парсинг моделей»: файл с колонкой «Актив»/«active_number», модалка с 4 полями-умолчаниями — Причина ("Убран десятый (лишний) межвагонный переход с 6 вагона"), Пользователь ("Велебская Александра Владимировна"), Склад ("Виртуальный склад"), Партия ("ЧСП ЛОМ") — применяются ко всему файлу разом (в отличие от исходного move_active, где «Откуда»/«Куда» читались из Excel построчно)
- [x] `_resolve_user_by_fullname`: ищет `fdw_users`, реконструируя ФИО тем же способом, что и `session['fullname']` в `auth.py` (`" ".join(filter(None, [lastname, firstname, middlename])) or username`) — так работает и без отчества
- [x] На актив: `INSERT INTO location` (id через `nextval`) → `INSERT INTO relocate` (старая/новая location, дата, пользователь, причина, `date_current`) → `UPDATE actives` (новая location, `lcn = 'S{склад}.{счётчик}'::ltree`, `id_actves_parent`/`id_actives_root` → NULL)
- [x] Починен известный баг оригинального `move_active`: там `storage.last_lcn` инкрементировался только в памяти Python и не сохранялся в БД — повторный запуск скрипта выдавал те же номера lcn повторно. Новая версия — тот же паттерн `FOR UPDATE` + инкремент + `UPDATE storage.last_lcn` в конце DO-блока, что уже использовался в «Создать именной актив» (Фаза 13)
- [x] Прогресс-бар переиспользует оверлей, добавленный для "set serial='none' lcn" (Фаза 17) — та же страница, тот же JS-инфраструктурный код
- [x] Добавлены тестовые хелперы `make_storage`/`make_consignment`/`make_user` в `tests/conftest.py`; 12 новых тестов (`tests/test_parser_move_actives_validation.py`): резолв склад/партия/пользователь (с отчеством и без), алиас колонки актива, дубликат в файле, актив не найден, сборка SQL-тела (в т.ч. с NULL старой location) — итого 112 тестов зелёных
- [x] Проверено на `grom-tk` в откатанной транзакции: реальный актив `ULP0090952` — `id_location` 6822904→7298623, `lcn` `S78.58123`→`S78.83938`, `storage.last_lcn` 83937→83938, запись в `relocate` создана корректно; после `rollback()` всё вернулось как было. Дефолтные Склад/Партия/Пользователь подтверждены существующими в БД
- Коммиты: см. следующий коммит после этой записи

## Фаза 19: Парсинг моделей — резолв активов по lsn в "Переместить активы", флажок NOCM, кнопка "Изменить lcn в модели" — ВЫПОЛНЕНА
- [x] «Переместить активы»: колонка «Актив»/«active_number» заменена на резолв через lsn/lcn — по той же логике, что и "set serial='none' lcn" (`_validate_and_build_serial_none_rows` + `_merge_serial_none_lcns`), затем один батч-запрос `SELECT ... WHERE lcn::text IN :lcns` находит реально существующие активы; строка без совпадения — не ошибка (позиция могла быть уже снята с части поездов)
- [x] «Переместить активы»: добавлен флажок «Установить позицию ТМЦ = 'NOCM'» (включён по умолчанию) — резолвит `design_number.id` по имени `'NOCM'` (не хардкод: id=74269 совпал на grom-tk и grom-prod, но резолв по имени переносим между профилями) и добавляет `id_design_number = ...` в `UPDATE actives`
- [x] Новая кнопка «Изменить lcn в модели» (между «Удалить строку в модели» и "set serial='none' lcn"): файл с колонками `lsn`/`lcn` (новое) и `Старый lsn`/`Старый lcn` (старое); id_train_type извлекается из обоих значений и должен совпадать; для каждого поезда этого типа строится пара реальных lcn (старый→новый)
- [x] **Двухфазный UPDATE** для «Изменить lcn в модели»: обнаружено на реальном файле — цепочки переименований (новый lcn одной пары = старый lcn другой, т.к. дочерняя позиция сдвигается вслед за родительской) ломали однопроходный `UPDATE ... FROM (VALUES...)` с `UniqueViolationError` на `actives_lcn_key`. Решение: сначала все затронутые активы получают временный `lcn` с префиксом `Z` (`'Z' || lcn::text` — не пересекается с реальными train/`M`/`S`-префиксами), затем одним запросом проставляются финальные значения; оба шага — в одной транзакции (`_run_change_lcn_execute`), скачиваемый .sql обёрнут в `BEGIN;`/`COMMIT;`
- [x] Порядок кнопок на странице: Добавить → Удалить → Изменить lcn в модели → set serial='none' lcn → Переместить активы
- [x] Тесты: +16 (`tests/test_parser_change_lcn_validation.py`: резолв пар, алиасы колонок, дубликаты/конфликты, несовпадение id_train_type, двухфазный SQL, коллизия-safe цепочка), `tests/test_parser_move_actives_validation.py` переписан под lsn-резолв (11→16 тестов, +5 на set_nocm) — итого 132 теста зелёных
- [x] Проверено на реальном файле пользователя (`grom-tk`, откатанные транзакции): «Переместить активы» — 8 строк листа «Удалить из моделей» → 19 реальных активов, `storage.last_lcn` +19, флажок NOCM проставил `id_design_number=74269` на всех 19; «Изменить lcn в модели» — 72 строки листа «Изменить LCN» → 342 пары, оба шага UPDATE по 342 rowcount без конфликтов уникальности, цепочка `268.2.5→268.2.5.4→268.2.5.4.1` применилась корректно, после `rollback()` всё вернулось как было
- Коммиты: см. следующий коммит после этой записи

## Фаза 20: Парсинг моделей — кнопка "Переместить активы без relocate" — ВЫПОЛНЕНА
- [x] Новая кнопка (после «Переместить активы»): та же валидация/пары lcn, что и у «Изменить lcn в модели» (`_validate_and_build_change_lcn_rows`, файл с колонками `lsn`/`Старый lsn`), но SQL дополнительно сбрасывает `id_actves_parent`/`id_actives_root` (позиция сменилась — старые ссылки на родителя/корень больше не актуальны, как в «Переместить активы»); `id_location` не трогается, в `relocate` ничего не пишется — соответственно не нужны поля Причина/Пользователь/Склад/Партия
- [x] Рефакторинг: `_build_change_lcn_sql_lines` и новый `_build_move_no_relocate_sql_lines` теперь оба — тонкие обёртки над общим `_build_two_phase_lcn_update_lines(valid_rows, extra_set="")` (тот же двухфазный `Z`-swap из Фазы 19, параметризован дополнительными `SET`-выражениями)
- [x] Тесты: +3 (`tests/test_parser_change_lcn_validation.py`: сброс parent/root в SQL, отсутствие `id_location`/`relocate` в сгенерированном SQL, пустой список) — итого 135 тестов зелёных
- [x] Проверено на реальном файле (лист «Изменить LCN», grom-tk, откатанная транзакция): 342 пары, оба шага по 342 rowcount; на конкретном активе `lcn` `276.1.6→276.1.6.4`, `id_actves_parent`/`id_actives_root` `VR1000001`/`VR1000000` → `NULL`, `id_location` не изменился, записей в `relocate` не создано; после `rollback()` всё вернулось как было
- Коммиты: см. следующий коммит после этой записи

## Следующие шаги
1. Разобраться с дублирующимися `car_place.name` в БД (443 группы дублей) — сейчас такие строки Excel просто помечаются как ошибка валидации и не обрабатываются
2. Покрыть happy-path `train_parser._validate_train_rows` (разрешение `car_place_id`/`id_actives_parent`) — нужен реальный Postgres в тестовом окружении (testcontainers или аналог), см. Фазу 9
3. Rate-limiting / защита от брутфорса на `/auth/login` — отмечалось в общем аудите проекта, пока не реализовано
