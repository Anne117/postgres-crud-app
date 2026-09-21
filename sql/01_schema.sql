-- =============================================================================
--  Практическое занятие №3. Создание спроектированной БД.
--  Предметная область: «склад интернет-магазина».
--  СУБД: PostgreSQL 18, БД appdb, локаль ru_RU.UTF-8.
--
--  4 связанные таблицы:
--    supplier    (поставщики)          — независимая
--    product     (товары)              — supplier_id  -> supplier(id)
--    "order"     (заказы)              — независимая
--    order_item  (позиции заказа)      — order_id    -> "order"(id)
--                                        product_id  -> product(id)
--
--  Скрипт идемпотентен: может выполняться повторно.
--  Выполняется от имени администратора БД (владельца схемы), например app_admin.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Таблицы
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS order_item, "order", product, supplier CASCADE;

CREATE TABLE supplier (
    id         integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       varchar(120) NOT NULL UNIQUE,
    country    varchar(60)  NOT NULL DEFAULT 'RU',
    rating     smallint     NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 100),
    created_at timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON TABLE supplier IS 'Поставщики товаров';

CREATE TABLE product (
    id          integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id integer      NOT NULL REFERENCES supplier (id) ON DELETE RESTRICT,
    sku         varchar(40)  NOT NULL UNIQUE,
    title       varchar(150) NOT NULL,
    price       numeric(12, 2) NOT NULL CHECK (price >= 0),
    stock       integer      NOT NULL DEFAULT 0 CHECK (stock >= 0),
    created_at  timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON TABLE product IS 'Товары (номенклатура)';

CREATE TABLE "order" (
    id         integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer   varchar(120)  NOT NULL,
    status     varchar(20)   NOT NULL DEFAULT 'new'
                             CHECK (status IN ('new', 'paid', 'shipped', 'cancelled')),
    total      numeric(14, 2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    created_at timestamptz   NOT NULL DEFAULT now()
);

COMMENT ON TABLE "order" IS 'Заказы покупателей';

CREATE TABLE order_item (
    id         integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id   integer       NOT NULL REFERENCES "order" (id) ON DELETE CASCADE,
    product_id integer       NOT NULL REFERENCES product (id) ON DELETE RESTRICT,
    quantity   integer       NOT NULL CHECK (quantity > 0),
    price      numeric(12, 2) NOT NULL CHECK (price >= 0),
    CONSTRAINT order_item_order_product_uniq UNIQUE (order_id, product_id)
);

COMMENT ON TABLE order_item IS 'Позиции (строки) заказов';

CREATE INDEX order_item_order_id_idx   ON order_item (order_id);
CREATE INDEX order_item_product_id_idx ON order_item (product_id);
CREATE INDEX product_supplier_id_idx   ON product (supplier_id);

-- ---------------------------------------------------------------------------
-- 2. Права пользователя БД, отличного от администратора БД и СУБД.
--    app_user — обычная роль: НЕ суперпользователь, НЕ владелец объектов,
--    без прав CREATE / DROP / DELETE.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD 'user_secret';
    END IF;
END
$$;

-- Явно фиксируем: роль не административная.
ALTER ROLE app_user NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

GRANT CONNECT ON DATABASE appdb TO app_user;
GRANT USAGE  ON SCHEMA public   TO app_user;

-- Просмотр (SELECT), вставка (INSERT), обновление (UPDATE) — по заданию.
GRANT SELECT, INSERT, UPDATE ON supplier, product, "order", order_item TO app_user;

-- Identity-последовательности (для INSERT с автоматическим id).
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;

-- DELETE явно не выдан.
REVOKE DELETE ON supplier, product, "order", order_item FROM app_user;

-- Новые объекты схемы public тоже получают базовые права.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_user;

COMMIT;
