# MEMORY.md — Накопленные знания и решения

## Communication
- Отвечать на русском
- Код, комменты, коммиты — на английском
- Без воды и повторения моего вопроса в ответе

## Code style
- Минимальный читаемый код, следовать стилю проекта
- Не добавлять docstrings/типы/комменты без просьбы
- Не рефакторить то, о чём не просили
- Не over-engineer

## Safety
- Никогда не коммитить .env, секреты, токены
- Никогда не хардкодить credentials
- Никаких rm -rf, DROP TABLE без подтверждения

## Архитектурные решения
- Выбран Litestar вместо FastAPI/Django из-за встроенного DI, строгой типизации и высокой производительности в асинхронных задачах.
- Серверный рендеринг через Jinja2 для SEO и быстрой первой отрисовки.
- Стилизация через Tailwind CDN для быстрого прототипирования без сборщиков.

## Известные нюансы Litestar
- Для подключения Jinja2 используется `TemplateConfig(engine=JinjaTemplateEngine(directory="templates"))`.
- Статические файлы регистрируются через `StaticFilesConfig(directories=["static"], path="/static")`.
- При запуске через `litestar run --reload` автоперезагрузка работает корректно только если точка входа импортируется без побочных эффектов.

## Уроки
- В Litestar 2.24 аргумент Template называется `template_name`, а не `name`. Это отличие от некоторых примеров в сети.
- Jinja2 подключается через `from litestar.contrib.jinja import JinjaTemplateEngine` + `from litestar.template.config import TemplateConfig`.
- Статика: `from litestar.static_files import StaticFilesConfig` (не `litestar.config.static_files`).
- `StaticFilesConfig` передаётся как список в `static_files_config=[...]`, параметр `path` — URL-префикс, `directories` — список путей на диске.
- Загрузка файлов: DI-парсинг `UploadFile` через параметр функции не работает — Litestar не распознаёт multipart form data. Использовать `await request.form()` и `.get("file")` напрямую.