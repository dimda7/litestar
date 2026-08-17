# Запуск Grom (Litestar) в Docker

Инструкция для развёртывания на другом ПК. Все команды выполняются **из корня репозитория**,
если не указано иное.

Содержимое папки:

| Файл | Назначение |
|---|---|
| `Dockerfile` | Сборка образа `litestar-grom:latest` (multi-stage, запуск от non-root `app`) |
| `docker-compose.yml` | Запуск контейнера, проброс порта, тома, `env_file` |
| `Dockerfile.dockerignore` | Что не попадает в build context (`venv`, `tests`, `.env`, `.idea` и т.д.) |

---

## 1. Требования на целевом ПК

- Docker Engine 24+ и плагин Compose v2 (`docker compose version`)
- Сетевой доступ к серверу PostgreSQL (адреса задаются в `.env`)
- Свободный порт `8011` (или другой — см. п. 4)

Пользователь должен состоять в группе `docker`, иначе все команды потребуют `sudo`:

```bash
sudo usermod -aG docker $USER
# нужен перелогин; в текущей сессии можно без него:
sg docker -c "docker ps"
```

---

## 2. Вариант А: сборка из исходников (основной)

```bash
git clone <repo-url> litestar
cd litestar
cp .env.example .env
```

Заполните `.env` (см. п. 3), затем:

```bash
docker compose -f images/docker-compose.yml up -d --build
```

Сборка занимает ~5–7 минут (основное время — слой `chown` на ~340 МБ зависимостей).

## 2. Вариант Б: перенос готового образа (без интернета / без исходников)

На машине, где образ уже собран:

```bash
docker save -o litestar-grom.tar litestar-grom:latest
```

Скопируйте на целевой ПК `litestar-grom.tar`, `images/docker-compose.yml` и заполненный `.env`.
Разложите так, чтобы `.env` лежал на уровень выше `docker-compose.yml` (как в репозитории):

```
grom/
├── .env
└── images/
    └── docker-compose.yml
```

Затем:

```bash
docker load -i litestar-grom.tar
cd grom
docker compose -f images/docker-compose.yml up -d          # без --build
```

> Без `--build` compose возьмёт уже загруженный образ `litestar-grom:latest`.
> Секция `build:` в этом случае не используется, наличие исходников не требуется.

---

## 3. Файл `.env`

Обязателен: без него `config.py` падает при старте. Берётся из `.env.example`,
**в образ не закладывается** — передаётся в контейнер через `env_file: ../.env`.

Минимально необходимое:

> Переменные `DB_*` — только первичный засев. Список подключений живёт в томе
> `config_data` и редактируется на странице Настройки (добавление, правка,
> удаление); `DB_*` читаются один раз, пока `config_data/db_profiles.json` не
> создан. Все три набора необязательны — набор с отсутствующей переменной
> пропускается.

| Переменная | Описание |
|---|---|
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Засев подключения `grom-tk` (`6432` — pgbouncer, `5432` — прямой postgres) |
| `DB_*_PROD`, `DB_*_MY` | Засев подключений `grom-prod` и `grom-my` |
| `JIIRA_HOST`, `JIIRA_PORT`, `JIIRA_USER`, `JIIRA_PASSWORD` | Jira: выбор вложения задачи вместо файла с диска |
| `SERVER_PORT` | Порт внутри контейнера, по умолчанию `8011` |
| `SESSION_SECRET` | Подпись cookie-сессии и CSRF-токена |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Сгенерировать секрет:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

`SESSION_SECRET` менять после запуска не нужно — это разлогинит всех пользователей.

**Не пишите комментарии в одной строке со значением.** Compose их срезает, а
`docker run --env-file` — нет, и `DB_PORT=6432   # комментарий` уронит старт с
`ValueError: invalid literal for int()`. Комментарий — только отдельной строкой.

Если БД живёт на самом хосте (а не на отдельном сервере), `DB_HOST=localhost` из контейнера
работать не будет — укажите IP хоста в локальной сети или `host.docker.internal`
с `extra_hosts: ["host.docker.internal:host-gateway"]` в compose.

---

## 4. Порты

В `docker-compose.yml`:

```yaml
ports:
  - "8011:8011"   # левое — порт хоста, правое — порт внутри контейнера (= SERVER_PORT)
```

Если 8011 на хосте занят (`Bind for 0.0.0.0:8011 failed: port is already allocated`) —
поменяйте **левую** часть, например `"8080:8011"`. Меняя `SERVER_PORT` в `.env`,
поправьте и правую часть.

Приложение доступно на `http://<host>:8011/`. Первый экран — выбор базы данных,
затем вход по учётным данным из `fdw_users` выбранной БД.

---

## 5. Управление

```bash
docker compose -f images/docker-compose.yml ps            # статус и healthcheck
docker compose -f images/docker-compose.yml logs -f        # логи uvicorn
docker compose -f images/docker-compose.yml restart        # перезапуск
docker compose -f images/docker-compose.yml down           # остановить и удалить контейнер
docker compose -f images/docker-compose.yml up -d --build  # пересобрать после изменений в коде
```

Данные переживают пересборку — они в именованных томах:

- `parser_data` → `/app/parser_data` (промежуточные JSON парсеров)
- `app_log` → `/app/log` (пофайловые логи модулей: `app.log`, `parser.log`, …)
- `config_data` → `/app/config_data` (подключения к БД, `db_profiles.json` — с паролями)

`down -v` удалит эти тома вместе с данными.

Healthcheck дёргает `/` каждые 30 с; `docker compose ps` покажет `healthy`.
Состояние `unhealthy` при живом контейнере обычно означает, что приложение стартовало,
но недоступно на `SERVER_PORT` внутри контейнера.

---

## 6. Диагностика

| Симптом | Причина |
|---|---|
| `permission denied ... /var/run/docker.sock` | Пользователь не в группе `docker` (п. 1) |
| `ValueError: invalid literal for int()` на старте | Инлайн-комментарий в `.env` (п. 3) |
| `port is already allocated` | Занят порт хоста (п. 4) |
| `MissingDependencyException: jinja2` | Образ собран из старого `requirements.txt` — пересоберите с `--build` |
| Контейнер стартует, но БД недоступна | `DB_HOST`/`DB_PORT` недостижимы из контейнера (п. 3) |

Разовый запуск для отладки без compose:

```bash
docker run --rm -p 8011:8011 --env-file .env litestar-grom:latest
```
