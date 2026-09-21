# Практическое занятие №3 — БД «склад интернет-магазина»

Вариант: 3 (создание БД, права пользователя, CRUD-приложение, Docker).

## Предметная область

Склад интернет-магазина. Четыре связанные таблицы:

```
supplier (поставщик)
   └─ id ──────────┐
                   │ supplier_id
             product (товар)
                   │ id
                   │ ▲ product_id
                   │ │
             order_item (позиция заказа)
                   │ order_id
                   ▼
              "order" (заказ)
```

* `supplier`   — поставщики (name, country, rating);
* `product`    — товары, ссылается на `supplier.id`;
* `"order"`    — заказы (customer, status, total);
* `order_item` — позиции заказов, ссылается на `"order".id` и `product.id`.

3 внешних ключа, 3 индекса по ссылкам, ограничения CHECK/UNIQUE/NOT NULL.

## Состав репозитория

| Файл | Назначение |
|------|------------|
| `sql/01_schema.sql` | создание 4 таблиц, индексов, роли `app_user` и её прав |
| `sql/02_seed.sql` | тестовые данные (идемпотентно, с выравниванием identity) |
| `sql/03_checks.sql` | проверочные SQL-запросы (6.1–6.3 «сырым» SQL, права роли) |
| `src/app.py` | интерактивное CRUD-приложение (6.1–6.4) |
| `src/sqlsafe.py` | безопасная сборка SQL: белый список колонок, `sql.Identifier`/`sql.Literal` |
| `src/db.py` | конфиг подключения (белый список ключей), метаданные, выполнение |
| `src/logger.py` | stdout/stderr + дружелюбные ошибки + дублирующий «сырой» лог |
| `tests/test_sqlsafe.py` | unit-тесты безопасности сборки SQL (инъекции) |
| `tests/test_seed_data.py` | unit-тесты заполнения БД демонстрационными данными |
| `tests/minidb.py` | разбор `02_seed.sql` для проверок без СУБД |
| `Dockerfile` | образ приложения (python:3.13-slim + psycopg + конфиг) |
| `docker/Dockerfile.db` | образ СУБД: postgres:18 + локаль ru_RU.UTF-8 |
| `docker/init.sql` | создание неадминистративной роли `app_user` при initdb |
| `docker/entrypoint.sh` | ожидание СУБД в сети Docker + запуск приложения |
| `crud.env` | Env-файл Docker с рабочими примерами значений (п. 6.8) |
| `docker-compose.yml` | СУБД + приложение в одной сети Docker (`env_file`) |

## Запуск

### 1. Собрать образы

```bash
docker compose build            # db (postgres18_ru) и app (crud-app:0.3.0)
docker build -t crud-app:0.3.0 .        # тегированный образ приложения
```

### 2. Поднять СУБД (таблицы и данные создаются автоматически при initdb)

```bash
docker compose up -d db
docker compose exec db psql -U app_admin -d appdb -c '\dt'
```

Файлы монтируются в `/docker-entrypoint-initdb.d/`:
`00_init.sql` (роль), `01_schema.sql` (таблицы + права), `02_seed.sql` (данные).

### 3. Интерактивное приложение

```bash
docker compose run --rm app
# или на хосте:
PGUSER=app_user PGPASSWORD=user_secret python src/app.py
# или с логином/паролем запросом в консоли:
python src/app.py
```

Демонстрация пунктов 6.1–6.4 — в меню приложения после выбора таблицы.

### 4. Unit-тесты

```bash
python -m unittest discover -s tests -v
# тест на реальной БД:
APP_TEST_DSN="host=localhost port=5432 dbname=appdb user=app_admin password=adminsecret" \
    python -m unittest discover -s tests -v
```

## Права пользователя `app_user`

`app_user` — не администратор БД и не администратор СУБД:

```sql
ALTER ROLE app_user NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT  CONNECT ON DATABASE appdb TO app_user;
GRANT  USAGE   ON SCHEMA public  TO app_user;
GRANT  SELECT, INSERT, UPDATE ON supplier, product, "order", order_item TO app_user;
REVOKE DELETE  ON supplier, product, "order", order_item FROM app_user;
```

Проверка: `psql -U app_user -d appdb -c 'DELETE FROM product WHERE id=1;'` →
дружелюбный отказ «нет прав», который приложение показывает как
«у вашей учётной записи нет прав на эту операцию».

## Безопасность передачи параметров

| Что вводит пользователь | Как защищено |
|---|---|
| название колонки/таблицы | белый список из `information_schema` + `psycopg.sql.Identifier` |
| значение | `psycopg.sql.Literal` (параметризация драйвером) |
| оператор сравнения | белый список `= <> > >= < <= IN` |
| логика условий | белый список `AND`/`OR` |
| логин/пароль | отдельные аргументы `connect()`, логин по regex |
| ключи конфига | белый список ключей + валидаторы |

Unit-тесты `tests/test_sqlsafe.py` подтверждают, что значения вида
`1; DROP TABLE supplier; --`, `' OR '1'='1`, `1 UNION SELECT ...` попадают
внутрь строкового литерала и не меняют структуру запроса, а имена колонок
`id; DROP TABLE supplier`, `id --`, `(id)` отклоняются.

## Логи (п. 6.5–6.7)

* успех → `stdout`, ошибка → `stderr`;
* сообщения об ошибках дружелюбные: без SQLSTATE, текстов драйвера,
  трассировок и текстов запросов;
* `APP_LOG_FILE` (или `--log-file`) — путь к файлу, куда дублируется
  технический журнал «в чистом виде» (SQL-текст, текст исключения,
  трассировка) без обработки для пользователя.

Пример Env-файла — `crud.env`:

```
PGUSER=app_user
PGPASSWORD=user_secret
APP_CONFIG=/opt/app/config/app.docker.conf
APP_LOG_FILE=/opt/app/logs/app.log
```

## Проверенные факты (реальный прогон)

| Проверка | Результат |
|---|---|
| `python -m unittest discover -s tests` | 40 тестов, OK |
| PostgreSQL в контейнере | `PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2)` |
| Локаль БД | `ru_RU.UTF-8` |
| `password_encryption` | `scram-sha-256` |
| `app_user`: SELECT/INSERT/UPDATE | разрешены (запросы выполняются) |
| `app_user`: DELETE / CREATE | отказано (нет прав) |
| Таблицы после initdb | 4 таблицы, 3+6+3+5 = 17 строк |
