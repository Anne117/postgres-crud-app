"""Unit-тесты безопасной сборки SQL (пункты 6.6 и «безопасная передача
параметров и названий колонок»).

Проверяют, что:
  * названия колонок, которых нет в таблице, отклоняются;
  * опасные «имена» колонок (id = 1; DROP TABLE ...) отклоняются;
  * значения всегда уезжают в запрос как литералы, а не как текст SQL
    (попытки инъекции через значение экранируются);
  * операторы и логика — только из белого списка.

Запуск:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sqlsafe as S  # noqa: E402

COLUMNS = {"id": "integer", "supplier_id": "integer", "sku": "character varying",
           "price": "numeric", "stock": "integer"}

INJECTIONS = [
    "1; DROP TABLE supplier; --",
    "1 OR 1=1",
    "' OR '1'='1",
    "1 UNION SELECT password FROM users",
    "1/*",
    'x" OR "1"="1',
]


class IdentifierTest(unittest.TestCase):
    def test_valid_column(self) -> None:
        ident = S.check_identifier("price", allowed=COLUMNS)
        self.assertIsInstance(ident, S.sql.Identifier)

    def test_unknown_column_rejected(self) -> None:
        with self.assertRaises(S.SqlBuildError):
            S.check_identifier("secret_column", allowed=COLUMNS)

    def test_injection_as_column_name_rejected(self) -> None:
        for bad in ("id; DROP TABLE supplier", "id --", "id = 1", "(id)", "id,price",
                    "1", "íd", "id'", 'id"'):
            with self.assertRaises(S.SqlBuildError, msg=f"принято: {bad!r}"):
                S.check_identifier(bad, kind="колонка")

    def test_injection_as_table_name_rejected(self) -> None:
        for bad in ("supplier; DROP TABLE x", "public.product", "product WHERE 1=1"):
            with self.assertRaises(S.SqlBuildError):
                S.check_identifier(bad, kind="таблица")


class OperatorTest(unittest.TestCase):
    def test_allowed_operators(self) -> None:
        for op in ("=", "<>", ">", ">=", "<", "<=", "in"):
            self.assertTrue(S.is_valid_operator(op), op)

    def test_forbidden_operators(self) -> None:
        for op in (";DROP", "OR", "--", "LIKE", "1=1"):
            self.assertFalse(S.is_valid_operator(op), op)
            with self.assertRaises(S.SqlBuildError):
                S.check_operator(op)

    def test_logical_operator_whitelist(self) -> None:
        S.check_logical_operator("and")
        with self.assertRaises(S.SqlBuildError):
            S.check_logical_operator("and 1=1 --")


class QueryTextTest(unittest.TestCase):
    """Проверяем текст собранного запроса (as_string с conn=None)."""

    def text(self, query) -> str:
        return query.as_string(None)

    def test_select_all(self) -> None:
        q = S.build_select("product", list(COLUMNS))
        self.assertEqual(self.text(q), 'SELECT "id", "supplier_id", "sku", "price", '
                                       '"stock" FROM "product"')

    def test_select_one_filter(self) -> None:
        q = S.build_select("product", ["id"], [{"column": "sku", "op": "=",
                                                "value": "SKU-1"}])
        self.assertEqual(self.text(q), 'SELECT "id" FROM "product" WHERE "sku" = '
                                       "'SKU-1'")

    def test_select_many_filters_and_logic(self) -> None:
        q = S.build_select(
            "product", ["id"],
            [{"column": "supplier_id", "op": "=", "value": 2},
             {"column": "price", "op": ">", "value": 1000}],
            "AND",
        )
        self.assertEqual(
            self.text(q),
            'SELECT "id" FROM "product" WHERE "supplier_id" = 2 AND "price" > 1000',
        )

    def test_in_operator(self) -> None:
        q = S.build_select("product", ["id"],
                           [{"column": "supplier_id", "op": "IN",
                             "value": [1, 2, 3]}])
        self.assertEqual(self.text(q),
                         'SELECT "id" FROM "product" WHERE "supplier_id" IN (1, 2, 3)')

    def test_values_are_escaped_not_executed(self) -> None:
        literal_re = re.compile(r"'(?:[^']|'')*'")
        for payload in INJECTIONS:
            q = S.build_select("product", ["id"],
                               [{"column": "sku", "op": "=", "value": payload}])
            text = self.text(q)
            # 1) всё значение целиком лежит внутри строкового литерала…
            self.assertIn(payload.replace("'", "''"),
                          literal_re.findall(text).pop())
            # 2) …а вне литералов подозрительных символов нет.
            outside = literal_re.sub("?", text)
            self.assertNotIn(";", outside, f"';' попал в структуру запроса: {text}")
            self.assertNotIn("DROP TABLE", outside, text)
            self.assertNotIn("UNION", outside, text)

    def test_update_one_row(self) -> None:
        q = S.build_update("product", {"price": 19990, "stock": 3},
                           [{"column": "id", "op": "=", "value": 1}])
        self.assertEqual(self.text(q),
                         'UPDATE "product" SET "price" = 19990, "stock" = 3 '
                         'WHERE "id" = 1')

    def test_update_many_rows(self) -> None:
        q = S.build_update("product", {"stock": 0},
                           [{"column": "supplier_id", "op": "IN", "value": [1, 2]}])
        self.assertEqual(self.text(q),
                         'UPDATE "product" SET "stock" = 0 WHERE "supplier_id" IN (1, 2)')

    def test_insert_one_row(self) -> None:
        q = S.build_insert("supplier", ["name", "rating"], [["ООО «Тест»", 50]])
        self.assertEqual(self.text(q),
                         'INSERT INTO "supplier" ("name", "rating") '
                         "VALUES ('ООО «Тест»', 50) RETURNING \"id\"")

    def test_insert_many_rows(self) -> None:
        q = S.build_insert("supplier", ["name", "rating"],
                           [["A", 1], ["B", 2]], returning=None)
        self.assertEqual(self.text(q),
                         'INSERT INTO "supplier" ("name", "rating") '
                         "VALUES ('A', 1), ('B', 2)")

    def test_update_without_where_rejected(self) -> None:
        with self.assertRaises(S.SqlBuildError):
            S.build_update("product", {"stock": 1}, [])

    def test_insert_width_mismatch_rejected(self) -> None:
        with self.assertRaises(S.SqlBuildError):
            S.build_insert("supplier", ["name", "rating"], [["A"]])

    def test_update_ignores_unknown_column(self) -> None:
        with self.assertRaises(S.SqlBuildError):
            S.build_update("product", {"id; DROP TABLE product": 1},
                           [{"column": "id", "op": "=", "value": 1}])


class CoerceTest(unittest.TestCase):
    def test_int(self) -> None:
        self.assertEqual(S.coerce_value("42", "integer"), 42)

    def test_bad_int(self) -> None:
        with self.assertRaises(S.SqlBuildError):
            S.coerce_value("42; DROP", "integer")

    def test_numeric(self) -> None:
        self.assertEqual(str(S.coerce_value("19990.50", "numeric")), "19990.50")

    def test_bool(self) -> None:
        self.assertIs(S.coerce_value("true", "boolean"), True)
        self.assertIs(S.coerce_value("нет", "boolean"), False)

    def test_null(self) -> None:
        self.assertIsNone(S.coerce_nullable("NULL", "integer"))
        self.assertIsNone(S.coerce_nullable(r"\N", "integer"))
        # пустая строка — это пустое значение, а не SQL NULL
        self.assertEqual(S.coerce_nullable("", "character varying"), "")

    def test_text_kept_as_is(self) -> None:
        payload = "'; DROP TABLE supplier; --"
        self.assertEqual(S.coerce_value(payload, "character varying"), payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
