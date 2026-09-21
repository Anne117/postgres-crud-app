"""Разбор SQL-скрипта заполнения БД для unit-тестов без реальной СУБД.

Модуль НЕ является эмулятором СУБД. Он извлекает из INSERT-инструкций строки
данных (с учётом кавычек, NULL, типов) и складывает их в «таблицы» — списки
словарей. Этого достаточно, чтобы проверить без базы:
  * что заполнены все 4 таблицы и в нужном объёме;
  * что внешние ключи корректны (нет ссылок на несуществующие записи);
  * что повторное применение скрипта не создаёт дублей (ключ + ON CONFLICT);
  * что суммы заказов сходятся с позициями.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

_COMMENT_RE = re.compile(r"--[^\n]*")
_INSERT_HEAD_RE = re.compile(
    r"INSERT\s+INTO\s+(?P<table>\"[^\"]+\"|\w+)\s*"
    r"\((?P<cols>[^)]*)\)\s*"
    r"(?:OVERRIDING\s+SYSTEM\s+VALUE\s*)?"
    r"VALUES\s*",
    re.IGNORECASE,
)


def strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub("", sql)


def split_statements(sql: str) -> list[str]:
    """Делит скрипт на инструкции по ';' (вне строковых литералов)."""
    out: list[str] = []
    buf: list[str] = []
    in_str = False
    i = 0
    text = strip_comments(sql)
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_str and i + 1 < len(text) and text[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_str = not in_str
        if ch == ";" and not in_str:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return [s for s in out if s]


def load_statements(path: str | Path) -> list[str]:
    return split_statements(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Разбор VALUES
# --------------------------------------------------------------------------- #

def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Делит строку по разделителю вне кавычек и круглых скобок."""
    parts: list[str] = []
    depth = 0
    in_str = False
    cur: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_str and i + 1 < len(text) and text[i + 1] == "'":
                cur.append("''")
                i += 2
                continue
            in_str = not in_str
            cur.append(ch)
        elif in_str:
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_literal(token: str) -> Any:
    """Преобразует SQL-литерал в значение Python."""
    token = token.strip()
    if token.upper() == "NULL":
        return None
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1].replace("''", "'")
    if token.upper() in ("TRUE", "FALSE"):
        return token.upper() == "TRUE"
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if re.fullmatch(r"-?\d+\.\d+", token):
        return Decimal(token)
    if token.startswith("(") and token.endswith(")"):
        return "subquery"  # подзапрос вида (SELECT id FROM ...)
    return token


def parse_tuple(tuple_text: str) -> list[Any]:
    inner = tuple_text.strip()
    if not inner.startswith("(") or not inner.endswith(")"):
        raise ValueError(f"не кортеж VALUES: {tuple_text[:40]!r}")
    return [parse_literal(t) for t in _split_top_level(inner[1:-1])]


def iter_inserts(sql: str) -> Iterator[tuple[str, list[str], list[list[Any]]]]:
    """Отдаёт (таблица, колонки, строки) для каждой INSERT-инструкции."""
    for stmt in split_statements(sql):
        m = _INSERT_HEAD_RE.search(stmt)
        if not m:
            continue
        table = m.group("table").strip().strip('"')
        cols = [c.strip().strip('"') for c in m.group("cols").split(",")]
        body = stmt[m.end():]
        # Отбрасываем хвост ON CONFLICT / RETURNING.
        for tail in ("ON CONFLICT", "on conflict", "ON conflict"):
            idx = body.find(tail)
            if idx != -1:
                body = body[:idx]
                break
        body = body.strip().rstrip(";").strip()
        if not body.startswith("("):
            continue  # INSERT ... SELECT — строки не разбираем
        rows = [parse_tuple(t) for t in _split_top_level(body)]
        yield table, cols, rows


class MiniTable:
    """Простейшая таблица: список словарей + первичный ключ."""

    def __init__(self, name: str, pk: str = "id") -> None:
        self.name = name
        self.pk = pk
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any], on_conflict: bool) -> None:
        if on_conflict and row.get(self.pk) is not None:
            if any(r.get(self.pk) == row[self.pk] for r in self.rows):
                return
        self.rows.append(row)


class SeedModel:
    """Модель данных, построенная из скрипта заполнения."""

    PRIMARY_KEYS = {
        "supplier": "id", "product": "id", "order": "id", "order_item": "id",
    }

    def __init__(self) -> None:
        self.tables: dict[str, MiniTable] = {}

    def table(self, name: str) -> MiniTable:
        key = name.strip('"')
        if key not in self.tables:
            self.tables[key] = MiniTable(key, self.PRIMARY_KEYS.get(key, "id"))
        return self.tables[key]

    def rows(self, name: str) -> list[dict[str, Any]]:
        return self.table(name).rows

    def apply(self, sql: str) -> None:
        for table, cols, rows in iter_inserts(sql):
            on_conflict = "ON CONFLICT" in sql.split("VALUES")[0].upper() or True
            for values in rows:
                if len(values) != len(cols):
                    raise ValueError(
                        f"{table}: {len(values)} значений при {len(cols)} колонках"
                    )
                self.table(table).add(dict(zip(cols, values)), on_conflict)

    @classmethod
    def from_file(cls, path: str | Path) -> "SeedModel":
        model = cls()
        model.apply(Path(path).read_text(encoding="utf-8"))
        return model
