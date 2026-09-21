"""Слой доступа к БД: конфиг, подключение, метаданные, выполнение запросов.

Безопасность подключения (развитие кода практического занятия №1):
  * настройки подключения — только из конфиг-файла (TOML) по строгому
    белому списку ключей и с проверкой значений; пользователь не может
    подсунуть собственную опцию драйвера (options=, target_session_attrs=…);
  * логин/пароль передаются драйверу ТОЛЬКО отдельными аргументами
    connect(user=..., password=...), никогда не склеиваются в DSN;
  * логин дополнительно ограничен регулярным выражением.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg import sql

ALLOWED_KEYS = {
    "host": (str, re.compile(r"^[A-Za-z0-9._\-\[\]:]+$")),
    "port": (int, range(1, 65536)),
    "dbname": (str, re.compile(r"^[A-Za-z0-9_\-]+$")),
    "sslmode": (str, {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}),
    "connect_timeout": (int, range(1, 3600)),
    "application_name": (str, re.compile(r"^[A-Za-z0-9_\-]{1,63}$")),
}

CONNECT_ARG_KEYS = ("host", "port", "dbname", "sslmode", "connect_timeout", "application_name")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# Схема, в которой работает приложение.
SCHEMA = "public"


@dataclass(frozen=True)
class DbConfig:
    host: str = "localhost"
    port: int = 5432
    dbname: str = "appdb"
    sslmode: str = "prefer"
    connect_timeout: int = 10
    application_name: str = "crud-app"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DbConfig":
        values: dict[str, Any] = {}
        for key, val in raw.items():
            if key not in ALLOWED_KEYS:
                raise ValueError(f"в конфиге недопустимый ключ: {key}")
            expected, guard = ALLOWED_KEYS[key]
            if expected is int:
                if isinstance(val, bool) or not isinstance(val, int):
                    raise ValueError(f"параметр {key} должен быть целым числом")
                if val not in guard:
                    raise ValueError(f"параметр {key} вне допустимого диапазона")
            elif isinstance(guard, re.Pattern):
                if not isinstance(val, str) or not guard.fullmatch(val):
                    raise ValueError(f"параметр {key} содержит недопустимое значение")
            elif isinstance(guard, (set, frozenset)):
                if not isinstance(val, str) or val not in guard:
                    raise ValueError(f"параметр {key} содержит недопустимое значение")
            else:
                if not isinstance(val, str):
                    raise ValueError(f"параметр {key} должен быть строкой")
            values[key] = val
        return cls(**values)

    def connect_kwargs(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in CONNECT_ARG_KEYS}


def load_config(path: str | Path) -> DbConfig:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"файл настроек не найден: {p}")
    import tomllib

    with open(p, "rb") as fh:
        data = tomllib.load(fh)
    section = data.get("database")
    if not isinstance(section, dict):
        raise ValueError(f"в файле {p} нет секции [database]")
    return DbConfig.from_dict(section)


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise ValueError("логин не может быть пустым")
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("логин может содержать только латинские буквы, цифры, '-' и '_'")
    return username


# --------------------------------------------------------------------------- #
# Подключение
# --------------------------------------------------------------------------- #

def connect(cfg: DbConfig, user: str, password: str) -> psycopg.Connection:
    """Открывает соединение. Логин и пароль — отдельными аргументами."""
    kwargs = cfg.connect_kwargs()
    kwargs["user"] = validate_username(user)
    kwargs["password"] = password
    conn = psycopg.connect(**kwargs)
    conn.autocommit = False
    return conn


# --------------------------------------------------------------------------- #
# Метаданные (для белого списка таблиц и колонок)
# --------------------------------------------------------------------------- #

def list_tables(conn: psycopg.Connection) -> list[str]:
    """Таблицы схемы public, которые видит текущий пользователь."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (SCHEMA,),
        )
        return [r[0] for r in cur.fetchall()]


def table_columns(conn: psycopg.Connection, table: str) -> dict[str, str]:
    """Словарь {колонка: тип данных} для таблицы — источник белого списка."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (SCHEMA, table),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def primary_key(conn: psycopg.Connection, table: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = %s AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """,
            (SCHEMA, table),
        )
        row = cur.fetchone()
        return row[0] if row else None


# --------------------------------------------------------------------------- #
# Выполнение и печать результата
# --------------------------------------------------------------------------- #

def run_select(conn: psycopg.Connection, query: sql.Composable,
               limit: int | None = None) -> tuple[list[str], list[tuple]]:
    """Выполняет SELECT, возвращает (заголовки, строки)."""
    with conn.cursor() as cur:
        cur.execute(query)
        headers = [d.name for d in (cur.description or [])]
        rows = cur.fetchall() if cur.description else []
    if limit is not None and len(rows) > limit:
        rows = rows[:limit]
    return headers, rows


def run_write(conn: psycopg.Connection, query: sql.Composable) -> int:
    """Выполняет INSERT/UPDATE, возвращает число затронутых строк.

    В psycopg 3 `with conn:` по выходу ЗАКРЫВАЕТ соединение, поэтому
    транзакция завершается явными commit()/rollback().
    """
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            affected = cur.rowcount
        conn.commit()
        return affected
    except Exception:
        conn.rollback()
        raise


def run_write_returning(conn: psycopg.Connection, query: sql.Composable) -> Any:
    """INSERT ... RETURNING value — возвращает значение."""
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Простой текстовый вывод таблицы."""
    if not headers:
        return "(нет колонок в результате)"
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))
    sep = "-+-".join("-" * w for w in widths)
    lines = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), sep]
    for row in cells:
        lines.append(" | ".join(v.ljust(widths[i]) for i, v in enumerate(row)))
    lines.append(f"({len(cells)} стр.)")
    return "\n".join(lines)
