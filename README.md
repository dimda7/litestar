# Grom (Litestar)

Веб-приложение для загрузки Excel-файлов и работы с данными grom в PostgreSQL.

## Установка в Docker на любом компьютере

Нужны Docker и сетевой доступ к серверу PostgreSQL. Python и другие зависимости
ставить не надо: всё собирается внутри образа.

### 1. Установите Docker

| Система | Что сделать |
|---|---|
| Linux | `curl -fsSL https://get.docker.com \| sudo sh`, затем `sudo usermod -aG docker $USER` и перелогиньтесь |
| Windows | Установите [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) с бэкендом WSL 2 и запустите его |
| macOS | Установите [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) и запустите его |

Проверка: `docker compose version` должна показать Compose v2.

### 2. Скачайте проект

```bash
git clone https://github.com/dimda7/litestar.git
cd litestar
```

Если git нет, скачайте ZIP со страницы репозитория и распакуйте его. Все команды
ниже выполняются из корня проекта.

### 3. Создайте `.env`

Linux, macOS:

```bash
cp .env.example .env
```

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
```

Сгенерируйте секрет сессий:

```bash
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_hex(32))"
```

Откройте `.env` и заполните два обязательных пункта:

- `SESSION_SECRET`: значение из команды выше. С пустым значением контейнер не запустится.
- Хотя бы один набор подключения к БД: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`,
  `DB_NAME`. Без него на экране выбора БД будет пусто и войти не получится: страница
  «Настройки», где редактируются подключения, доступна только после входа.

Пример подключения:

```dotenv
DB_HOST=192.168.1.10
DB_PORT=6432
DB_USER=grom
DB_PASSWORD='пароль'
DB_NAME=grom
```

Пишите комментарии только отдельной строкой. Пароль берите в одинарные кавычки:
без них Docker Compose воспримет `$` как начало переменной и молча обрежет пароль.

Если PostgreSQL работает на этом же компьютере, `localhost` из контейнера не сработает:
укажите `DB_HOST=host.docker.internal`. В Linux дополнительно добавьте сервису `app`
в `images/docker-compose.yml`:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

### 4. Запустите

```bash
docker compose -f images/docker-compose.yml up -d --build
```

Первая сборка занимает несколько минут.

Проверьте, что статус контейнера стал `healthy`:

```bash
docker compose -f images/docker-compose.yml ps
```

### 5. Откройте приложение

Перейдите на <http://localhost:8011/> (или `http://<адрес-компьютера>:8011/` с другого
устройства), выберите базу данных и войдите под пользователем из `fdw_users` этой базы.

### Обновление

```bash
git pull
docker compose -f images/docker-compose.yml up -d --build
```

Подключения, логи и данные парсеров хранятся в томах Docker и сохраняются при
пересборке. Значения `DB_*` читаются только при первом запуске: чтобы потом
изменить подключение, используйте страницу «Настройки», а не `.env`.

Перенос готового образа без интернета, смена порта, управление контейнером и диагностика
описаны в [images/README.md](images/README.md).
