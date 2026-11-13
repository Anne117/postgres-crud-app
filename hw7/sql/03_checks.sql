-- =============================================================================
-- 03_checks.sql — проверочные SQL-скрипты (демонстрация пункта 6.1–6.3
--  «сырым» SQL и проверка прав пользователя app_user).
--  Запуск:  psql -U app_admin -d appdb -f sql/03_checks.sql
--           psql -U app_user  -d appdb -f sql/03_checks.sql
-- =============================================================================

\echo '--- 6.1 Без фильтрации ---'
SELECT * FROM supplier;

\echo '--- 6.1 Все товары с поставщиками (связь 4 таблиц) ---'
SELECT o.id AS order_id, o.customer, o.status,
       p.title AS product, s.name AS supplier,
       oi.quantity, oi.price AS item_price,
       (oi.quantity * oi.price) AS position_sum
FROM "order" o
JOIN order_item oi ON oi.order_id = o.id
JOIN product    p  ON p.id = oi.product_id
JOIN supplier   s  ON s.id = p.supplier_id
ORDER BY o.id, p.title;

\echo '--- 6.2 Фильтрация по одному значению ---'
SELECT * FROM product WHERE supplier_id = 2;

\echo '--- 6.3 Фильтрация по нескольким значениям ---'
SELECT * FROM product WHERE supplier_id = 2 AND price > 1000;

\echo '--- Права роли app_user (SELECT/INSERT/UPDATE, без DELETE) ---'
SELECT grantee, table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE grantee = 'app_user'
  AND table_name IN ('supplier', 'product', 'order_item', 'order')
GROUP BY grantee, table_name
ORDER BY table_name;
