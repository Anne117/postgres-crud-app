"""Безопасная сборка SQL-запросов из данных, введённых пользователем.

Ключевой принцип: НИ ОДНО значение из пользовательского ввода не попадает в
текст SQL напрямую.

  * Названия колонок и таблиц проверяются по «белому списку» (метаданным,
    прочитанным из самой БД), затем подставляются объектом
    ``psycopg.sql.Identifier`` — драйвер сам экранирует их по правилам СУБД.
  * Значения подставляются объектом ``psycopg.sql.Literal`` — драйвер сам
    заключает их в кавычки/приводит типы.
  * Литерал (``sql.SQL("...")``) используется только для текста запроса,
    собранного из проверенных фрагментов.

Так исключаются и «классические» SQL-инъекции (``1; DROP TABLE ...``,
``' OR 1=1 --``), и инъекции через идентификаторы (``id = 1; DROP ...``).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from psycopg import sql

# Разрешённые операторы сравнения (белый список).
COMPARISON_OPERATORS = {
    "=": sql.SQL("="),
    "<>": sql.SQL("<>"),
    ">": sql.SQL(">"),
    ">=": sql.SQL(">="),
    "<": sql.SQL("<"),
    "<=": sql.SQL("<="),
}

LOGICAL_OPERATORS = {"AND": sql.SQL(" AND "), "OR": sql.SQL(" OR ")}

# Оператор IN обрабатывается отдельно (список значений).
SPECIAL_OPERATORS = {"IN", "NOT IN"}

# Имя колонки/таблицы должно быть «обычным» идентификатором. Дополнительная
# (вторая) линия защиты поверх белого списка метаданных.
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

BOOL_TRUE = {"1", "true", "t", "yes", "y", "да", "истина"}
BOOL_FALSE = {"0", "false", "f", "no", "n", "нет", "ложь"}


class SqlBuildError(ValueError):
    """Ошибка сборки запроса — показывается пользователю в дружелюбном виде."""


# --------------------------------------------------------------------------- #
# Проверка идентификаторов
# --------------------------------------------------------------------------- #

def check_identifier(value: str, *, allowed: Iterable[str] | None = None,
                     kind: str = "колонка") -> sql.Identifier:
    """Проверяет имя колонки/таблицы и возвращает безопасный Identifier."""
    if not isinstance(value, str):
        raise SqlBuildError(f"некорректное имя {kind}")
    value = value.strip()
    if not IDENT_RE.match(value):
        raise SqlBuildError(
            f"недопустимое имя {kind}: допускаются латинские буквы, цифры и '_'"
        )
    if allowed is not None and value not in set(allowed):
        raise SqlBuildError(f"неизвестная {kind}: {value}")
    return sql.Identifier(value)


def check_operator(op: str) -> Any:
    """Проверяет оператор сравнения по белому списку."""
    key = str(op).strip().upper()
    if key in SPECIAL_OPERATORS:
        return key
    if key not in COMPARISON_OPERATORS:
        raise SqlBuildError(
            "недопустимый оператор сравнения: разрешены "
            + ", ".join(sorted(COMPARISON_OPERATORS) + sorted(SPECIAL_OPERATORS))
        )
    return COMPARISON_OPERATORS[key]


def is_valid_operator(op: str) -> bool:
    """Проверяет, входит ли оператор в белый список."""
    key = str(op).strip().upper()
    return key in COMPARISON_OPERATORS or key in SPECIAL_OPERATORS


def check_logical_operator(op: str) -> sql.SQL:
    key = str(op).strip().upper()
    if key not in LOGICAL_OPERATORS:
        raise SqlBuildError("недопустимый логический оператор: разрешены AND, OR")
    return LOGICAL_OPERATORS[key]


# --------------------------------------------------------------------------- #
# Приведение типов значений
# --------------------------------------------------------------------------- #

def _to_number(raw: str):
    try:
        if "." in raw:
            return Decimal(raw)
        return int(raw)
    except (ValueError, InvalidOperation):
        raise SqlBuildError(f"значение {raw!r} не является числом") from None


def coerce_value(raw: str, col_type: str):
    """Приводит введённый текст к типу колонки.

    Нужно не для безопасности (её обеспечивает sql.Literal), а чтобы СУБД
    корректно сравнивала значения и чтобы ошибка была понятной.
    """
    if raw is None:
        return None
    raw = raw.strip()
    t = (col_type or "").lower()

    # Массивы PostgreSQL — поддерживаем список значений.
    if t.endswith("[]"):
        inner = t[:-2]
        items = [x.strip() for x in raw.strip("{}").split(",") if x.strip()]
        return [coerce_value(x, inner) for x in items]

    if any(k in t for k in ("int", "serial")):
        try:
            return int(raw)
        except ValueError:
            raise SqlBuildError(f"значение {raw!r} не похоже на целое число") from None

    if any(k in t for k in ("numeric", "decimal", "real", "double")):
        return _to_number(raw)

    if any(k in t for k in ("timestamp", "date", "time")):
        return raw  # передаётся литералом, СУБД сама разбирает ISO-формат

    if "bool" in t:
        low = raw.lower()
        if low in BOOL_TRUE:
            return True
        if low in BOOL_FALSE:
            return False
        raise SqlBuildError(f"значение {raw!r} не является логическим (true/false)")

    if "uuid" in t or "json" in t:
        return raw

    return raw  # text, varchar, char, enum и т.п.


def coerce_nullable(raw: str, col_type: str):
    """Как coerce_value, но строка NULL (или \\N) означает SQL NULL."""
    if raw is None:
        return None
    if raw.strip().lower() in ("null", r"\n"):
        return None
    return coerce_value(raw, col_type)


# --------------------------------------------------------------------------- #
# Сборка запросов
# --------------------------------------------------------------------------- #

def build_select(table: str, columns: Sequence[str],
                 filters: Sequence[Mapping[str, Any]] | None = None,
                 logical: str = "AND",
                 limit: int | None = None) -> sql.Composed:
    """SELECT c1, c2 FROM t [WHERE c = %s AND ...] [LIMIT n]."""
    ident_t = check_identifier(table, kind="таблица")
    select_list = (
        sql.SQL(" *")
        if not columns
        else sql.SQL(", ").join(check_identifier(str(c), kind="колонка")
                                for c in columns)
    )
    parts: list[sql.Composable] = [
        sql.SQL("SELECT "), select_list, sql.SQL(" FROM "), ident_t,
    ]

    if filters:
        parts.append(sql.SQL(" WHERE "))
        parts.append(build_where(filters, logical))

    if limit is not None:
        parts.append(sql.SQL(" LIMIT ")); parts.append(sql.Literal(int(limit)))

    return sql.Composed(parts)


def build_where(filters: Sequence[Mapping[str, Any]], logical: str = "AND") -> sql.Composed:
    """Собирает условие WHERE из списка фильтров.

    Каждый фильтр: {"column": ..., "op": ..., "value": ...}
    Значение передаётся литералом через sql.Literal -> параметризация.
    """
    if not filters:
        raise SqlBuildError("не задано ни одного условия фильтрации")
    # Оператор берётся из белого списка (check_logical_operator), поэтому
    # вставка его текста в запрос безопасна.
    joiner = check_logical_operator(logical)
    chunks = []
    for f in filters:
        col = check_identifier(str(f["column"]), kind="колонка")
        key = str(f.get("op", "=")).strip().upper()
        op = check_operator(key)
        value = f.get("value")
        if key in SPECIAL_OPERATORS:
            # Оператор IN / NOT IN: список значений одного типа.
            items = value if isinstance(value, (list, tuple)) else [value]
            if not items:
                raise SqlBuildError("для оператора IN нужен хотя бы один элемент")
            chunks.append(sql.Composed([
                col, sql.SQL(" "), sql.SQL(op), sql.SQL(" ("),
                sql.SQL(", ").join(sql.Literal(v) for v in items),
                sql.SQL(")"),
            ]))
            continue
        chunks.append(
            sql.Composed([col, sql.SQL(" "), op, sql.SQL(" "),
                          sql.Literal(value)])
        )
    return joiner.join(chunks)


def build_update(table: str, assignments: Mapping[str, Any],
                 filters: Sequence[Mapping[str, Any]],
                 logical: str = "AND") -> sql.Composed:
    """UPDATE t SET c1 = v1, ... WHERE ... (имена колонок проверяются)."""
    if not assignments:
        raise SqlBuildError("не задано ни одного поля для обновления")
    set_parts = [
        sql.Composed([check_identifier(str(c), kind="колонка"),
                      sql.SQL(" = "), sql.Literal(v)])
        for c, v in assignments.items()
    ]
    return sql.Composed([
        sql.SQL("UPDATE "), check_identifier(table, kind="таблица"),
        sql.SQL(" SET "), sql.SQL(", ").join(set_parts),
        sql.SQL(" WHERE "), build_where(filters, logical),
    ])


def build_insert(table: str, columns: Sequence[str],
                 rows: Sequence[Sequence[Any]],
                 returning: str | None = "id") -> sql.Composed:
    """INSERT INTO t (c1, c2) VALUES (...), (...) [RETURNING id]."""
    if not columns:
        raise SqlBuildError("не задан список колонок для вставки")
    if not rows:
        raise SqlBuildError("не задано ни одной строки для вставки")
    width = len(columns)
    value_groups = []
    for row in rows:
        if len(row) != width:
            raise SqlBuildError("число значений не совпадает с числом колонок")
        value_groups.append(
            sql.SQL("(") + sql.SQL(", ").join(sql.Literal(v) for v in row) + sql.SQL(")")
        )
    parts = [
        sql.SQL("INSERT INTO "), check_identifier(table, kind="таблица"),
        sql.SQL(" (")
        + sql.SQL(", ").join(check_identifier(str(c), kind="колонка") for c in columns)
        + sql.SQL(")"),
        sql.SQL(" VALUES "), sql.SQL(", ").join(value_groups),
    ]
    if returning:
        parts += [sql.SQL(" RETURNING "), check_identifier(returning, kind="колонка")]
    return sql.Composed(parts)


def render(query: sql.Composable, conn) -> str:
    """Текст запроса с подставленными значениями — для журнала/показа."""
    return query.as_string(conn if conn is not None else None)
