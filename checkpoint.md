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

## Следующие шаги
1. Настроить кастомные error pages (404, 500)
