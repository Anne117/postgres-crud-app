-- =============================================================================
--  init.sql — выполняется один раз при первой инициализации кластера
--  (монтируется в /docker-entrypoint-initdb.d/). Выполняется от имени
--  администратора БД (POSTGRES_USER=app_admin).
--
--  Здесь создаётся пользователь, отличный от администратора БД и СУБД.
--  Создание таблиц и выдача прав — в sql/01_schema.sql, который монтируется
--  в тот же каталог и выполняется после этого файла.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD 'user_secret';
    END IF;
END
$$;

-- Роль не является административной: ни суперпользователь, ни владелец БД,
-- ни возможность создавать объекты.
ALTER ROLE app_user NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

GRANT CONNECT ON DATABASE appdb TO app_user;
GRANT USAGE   ON SCHEMA public  TO app_user;
