# HANDOFF.md

## В процессе

### 1. Happy-path `train_parser._validate_train_rows` не покрыт тестами
`tests/test_train_parser_validation.py` останавливается перед веткой с `text("... WHERE lcn::text = :lcn")` — оператор `::text` не парсится SQLite, поэтому разрешение `car_place_id`/`id_actives_parent` не протестировано.
**Следующий шаг**: поднять реальный Postgres в тестовом окружении (testcontainers или аналог; на момент фазы 9 `docker` был без доступа к сокету) и дописать happy-path тесты.

### 2. Rate-limiting на `/auth/login` отсутствует
Форма логина не защищена от брутфорса.
**Следующий шаг**: добавить ограничение попыток по IP/логину (throttling-middleware или счётчик в БД/кеше) в `controllers/auth.py`.
