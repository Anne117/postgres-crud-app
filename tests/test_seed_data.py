"""Unit-тесты заполнения БД демонстрационными данными (задание, п. 5).

Запуск БЕЗ базы данных (по умолчанию) — проверяются сценарии заполнения,
которые тест применяет к модели «мини-СУБД» (см. tests/minidb.py):

    python -m unittest discover -s tests -v

Запуск С реальной БД в Docker (интеграционная проверка):

    APP_TEST_DSN="host=localhost port=5432 dbname=appdb user=app_admin password=adminsecret" \
        python -m unittest tests.test_seed_data -v -k real
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from minidb import SeedModel, load_statements  # noqa: E402

SEED = ROOT / "sql" / "02_seed.sql"
SCHEMA = ROOT / "sql" / "01_schema.sql"


class SeedStatementsTest(unittest.TestCase):
    """Проверка того, что скрипт заполнения реально содержит нужные данные."""

    def setUp(self) -> None:
        self.statements = load_statements(SEED)
        text = SEED.read_text(encoding="utf-8")
        self.text = text

    def test_script_exists(self) -> None:
        self.assertTrue(SEED.exists(), "не найден sql/02_seed.sql")

    def test_transaction_wrapped(self) -> None:
        upper = [s.strip().upper() for s in self.statements]
        self.assertEqual(upper[0], "BEGIN")
        self.assertIn("COMMIT", upper, "скрипт не завершает транзакцию")

    def test_all_four_tables_filled(self) -> None:
        joined = "\n".join(self.statements).lower()
        for table in ("supplier", "product", '"order"', "order_item"):
            self.assertIn(f"insert into {table}", joined,
                          f"в скрипте нет вставки в {table}")

    def test_script_is_idempotent(self) -> None:
        for stmt in self.statements:
            if stmt.strip().upper().startswith("INSERT"):
                self.assertIn("ON CONFLICT", stmt.upper(),
                              "нет ON CONFLICT — повторный запуск даст дубли")

    def test_identity_sequences_are_realigned(self) -> None:
        self.assertIn("setval", self.text.lower())

    def test_demo_data_volume(self) -> None:
        joined = "\n".join(self.statements).upper()
        self.assertGreaterEqual(joined.count("SKU-"), 6, "меньше 6 товаров")
        self.assertGreaterEqual(joined.count("OVERRIDING SYSTEM VALUE"), 4,
                                "данные вставлены без явных id")


class SchemaStatementsTest(unittest.TestCase):
    """Проверка структуры: 4 связанные таблицы и права неадминистративной роли."""

    def setUp(self) -> None:
        self.text = SCHEMA.read_text(encoding="utf-8")
        self.lower = self.text.lower()

    def test_four_tables(self) -> None:
        for table in ("supplier", "product", '"order"', "order_item"):
            self.assertIn(f"create table {table}", self.lower)

    def test_foreign_keys(self) -> None:
        self.assertEqual(self.lower.count("references"), 3,
                         "ожидаются 3 внешних ключа (product->supplier, "
                         "order_item->order, order_item->product)")

    def test_non_admin_role(self) -> None:
        self.assertIn("create role app_user", self.lower)
        self.assertIn("nosuperuser", self.lower)
        self.assertIn("nocreatedb", self.lower)
        self.assertIn("nocreaterole", self.lower)

    def test_grants_without_delete(self) -> None:
        self.assertIn("grant select, insert, update on", self.lower)
        self.assertIn("revoke delete", self.lower)


class DemoDataTest(unittest.TestCase):
    """Функциональный тест: заполняем модель данных и проверяем связи."""

    def setUp(self) -> None:
        self.db = SeedModel()

    def seed(self) -> None:
        self.db = SeedModel.from_file(SEED)

    def test_seed_fills_all_tables(self) -> None:
        self.seed()
        self.assertEqual(len(self.db.rows("supplier")), 3)
        self.assertEqual(len(self.db.rows("product")), 6)
        self.assertEqual(len(self.db.rows("order")), 3)
        self.assertEqual(len(self.db.rows("order_item")), 5)

    def test_links_are_valid(self) -> None:
        self.seed()
        supplier_ids = {r["id"] for r in self.db.rows("supplier")}
        product_by_id = {r["id"]: r for r in self.db.rows("product")}
        for p in self.db.rows("product"):
            self.assertIn(p["supplier_id"], supplier_ids,
                          "товар ссылается на несуществующего поставщика")
        order_ids = {r["id"] for r in self.db.rows("order")}
        for item in self.db.rows("order_item"):
            self.assertIn(item["order_id"], order_ids)
            self.assertIn(item["product_id"], product_by_id)

    def test_repeat_seed_is_idempotent(self) -> None:
        self.seed()
        before = {t: len(self.db.rows(t)) for t in
                  ("supplier", "product", "order", "order_item")}
        for stmt in load_statements(SEED):
            if stmt.strip().upper().startswith("INSERT"):
                self.db.apply(stmt)
        after = {t: len(self.db.rows(t)) for t in
                 ("supplier", "product", "order", "order_item")}
        self.assertEqual(before, after, "повторное заполнение создало дубли")

    def test_order_totals_match_positions(self) -> None:
        self.seed()
        for order in self.db.rows("order"):
            positions = [i for i in self.db.rows("order_item")
                         if i["order_id"] == order["id"]]
            total = sum(i["quantity"] * i["price"] for i in positions)
            self.assertAlmostEqual(float(order["total"]), float(total), places=2,
                                   msg=f"сумма заказа {order['id']} не сходится")


class RealDatabaseSeedTest(unittest.TestCase):
    """Интеграционная проверка на реальной БД (нужен запущенный PostgreSQL)."""

    dsn = os.environ.get("APP_TEST_DSN", "")

    @unittest.skipUnless(dsn, "APP_TEST_DSN не задан — тест с реальной БД пропущен")
    def test_real_seed(self) -> None:
        import psycopg  # локальный импорт: без БД модуль не требуется

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                for path in (SCHEMA, SEED):
                    cur.execute(path.read_text(encoding="utf-8"))
                conn.commit()
                cur.execute("SELECT count(*) FROM supplier")
                self.assertEqual(cur.fetchone()[0], 3)
                cur.execute("SELECT count(*) FROM product")
                self.assertEqual(cur.fetchone()[0], 6)
                cur.execute("SELECT count(*) FROM order_item")
                self.assertEqual(cur.fetchone()[0], 5)
                # повторный прогон не должен ломаться и создавать дубли
                cur.execute(SEED.read_text(encoding="utf-8"))
                conn.commit()
                cur.execute("SELECT count(*) FROM product")
                self.assertEqual(cur.fetchone()[0], 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
