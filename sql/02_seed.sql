-- =============================================================================
--  Практическое занятие №3. Демонстрационные данные (тестовый SQL-скрипт).
--
--  Скрипт идемпотентен: значения id заданы явно, повторный запуск не создаёт
--  дублей (ON CONFLICT (id) DO NOTHING). После вставки identity-последовательности
--  выравниваются по максимальному id, чтобы приложение могло вставлять строки
--  без конфликтов.
--
--  Выполняется от имени администратора БД (app_admin) после 01_schema.sql.
-- =============================================================================

BEGIN;

-- --- supplier ---------------------------------------------------------------
INSERT INTO supplier (id, name, country, rating) OVERRIDING SYSTEM VALUE VALUES
    (1, 'ООО «ТехноСнаб»',      'RU', 85),
    (2, 'Shenzhen Micro Ltd',   'CN', 72),
    (3, 'Baltic Trade GmbH',    'DE', 60)
ON CONFLICT (id) DO NOTHING;

-- --- product ----------------------------------------------------------------
INSERT INTO product (id, supplier_id, sku, title, price, stock) OVERRIDING SYSTEM VALUE VALUES
    (1, 1, 'SKU-1001', 'Ноутбук 14"',            74990.00,  12),
    (2, 1, 'SKU-1002', 'Монитор 27"',            21990.00,  30),
    (3, 2, 'SKU-2001', 'Кабель USB-C 2 м',         490.00, 500),
    (4, 2, 'SKU-2002', 'Клавиатура механическая', 6490.00,  45),
    (5, 3, 'SKU-3001', 'Кресло офисное',         15990.00,   8),
    (6, 3, 'SKU-3002', 'Стол письменный',        12490.00,   6)
ON CONFLICT (id) DO NOTHING;

-- --- order ------------------------------------------------------------------
INSERT INTO "order" (id, customer, status, total) OVERRIDING SYSTEM VALUE VALUES
    (1, 'Иванов И.И.',  'paid',    75970.00),
    (2, 'Петрова А.С.', 'new',     22480.00),
    (3, 'Сидоров П.П.', 'shipped',159900.00)
ON CONFLICT (id) DO NOTHING;

-- --- order_item -------------------------------------------------------------
INSERT INTO order_item (id, order_id, product_id, quantity, price)
OVERRIDING SYSTEM VALUE VALUES
    (1, 1, 1,  1, 74990.00),
    (2, 1, 3,  2,   490.00),
    (3, 2, 2,  1, 21990.00),
    (4, 2, 3,  1,   490.00),
    (5, 3, 5, 10, 15990.00)
ON CONFLICT (id) DO NOTHING;

-- --- выравнивание identity-последовательностей ------------------------------
SELECT setval(pg_get_serial_sequence('supplier',   'id'),
              COALESCE((SELECT max(id) FROM supplier),   1), true);
SELECT setval(pg_get_serial_sequence('product',    'id'),
              COALESCE((SELECT max(id) FROM product),    1), true);
SELECT setval(pg_get_serial_sequence('"order"',    'id'),
              COALESCE((SELECT max(id) FROM "order"),    1), true);
SELECT setval(pg_get_serial_sequence('order_item', 'id'),
              COALESCE((SELECT max(id) FROM order_item), 1), true);

COMMIT;

-- Проверка: сколько строк в каждой таблице.
SELECT 'supplier'   AS table_name, count(*) AS rows FROM supplier
UNION ALL SELECT 'product',    count(*) FROM product
UNION ALL SELECT 'order',      count(*) FROM "order"
UNION ALL SELECT 'order_item', count(*) FROM order_item
ORDER BY 1;
