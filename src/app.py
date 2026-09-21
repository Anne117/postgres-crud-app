#!/usr/bin/env python3
"""Практическое занятие №3. Интерактивное CRUD-приложение над PostgreSQL.

Предметная область: «склад интернет-магазина»
(supplier -> product -> order_item <- "order").

Возможности (пункты 6.1–6.4 задания):
  6.1 просмотр таблиц без фильтрации                      SELECT * FROM t;
  6.2 просмотр с фильтрацией по одному значению           ... WHERE col = value;
  6.3 просмотр с фильтрацией по нескольким значениям      ... WHERE c1 = v1 AND c2 = v2;
  6.4 обновление одной записи (любые поля, кроме id);
      обновление нескольких записей общим значением       ... WHERE col IN (v1, v2);
      вставка одной строки;
      вставка одной строки в несколько связанных таблиц   (INSERT ... RETURNING id);
      вставка нескольких строк в одну таблицу;
      вставка нескольких строк в несколько связанных таблиц.

Безопасность:
  * имена колонок и таблиц — только из белого списка (метаданные information_schema)
    и передаются объектом sql.Identifier;
  * все значения — объектом sql.Literal (параметризация драйвером psycopg 3);
  * настройки подключения — только из конфиг-файла по белому списку ключей;
  * логин/пароль — отдельными аргументами connect(), логин ограничен регуляркой.

Логи:
  * успех -> stdout, ошибки -> stderr; тексты ошибок дружелюбные, без деталей
    реализации (SQLSTATE, тексты драйвера, трассировки);
  * переменная окружения APP_LOG_FILE (или --log-file) включает дублирование
    подробного технического журнала в файл, «в чистом виде», без обработки.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any, Sequence

import psycopg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db as dbmod          # noqa: E402
import sqlsafe as S         # noqa: E402
from logger import Logger   # noqa: E402

MENU = """
=========== Таблица "{table}" | пользователь: {user} ===========
  1) 6.1 Показать все строки (без фильтрации)
  2) 6.2 Показать строки с фильтром по значению
  3) 6.3 Показать строки с несколькими фильтрами
  4) 6.4 Обновить одну запись (любые поля, кроме id)
  5) 6.4 Обновить несколько записей общим значением
  6) 6.4 Вставить одну строку
  7) 6.4 Вставить несколько строк
  8) 6.4 Вставить строку в две связанные таблицы
  9) Показать структуру таблиц (связи)
  0) Выбрать другую таблицу
  q) Выход
Ваш выбор:"""


# --------------------------------------------------------------------------- #
# Ввод
# --------------------------------------------------------------------------- #

class Quit(Exception):
    """Пользователь прервал ввод (Ctrl+D / пустой stdin)."""


def ask(prompt: str, default: str | None = None) -> str:
    """Читает строку у пользователя. Пустой ввод -> значение по умолчанию."""
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{prompt}{suffix}: ")
    except EOFError:
        raise Quit() from None
    raw = raw.strip()
    if not raw and default is not None:
        return default
    return raw


def ask_yes(prompt: str, default_no: bool = True) -> bool:
    d = "N" if default_no else "Y"
    ans = ask(prompt + " (y/N)" if default_no else prompt + " (Y/n)", d).lower()
    return ans in ("y", "yes", "д", "да")


def pick_column(columns: dict[str, str], prompt: str = "Колонка",
                allow_blank: bool = False) -> str:
    """Выбор колонки по номеру из списка или по имени (с белым списком)."""
    names = list(columns)
    while True:
        raw = ask(prompt)
        if raw == "":
            if allow_blank:
                return ""
            print("Нужно указать колонку.", file=sys.stderr)
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        if raw in columns:
            return raw
        print("Нет такой колонки. Укажите номер из списка или точное имя.",
              file=sys.stderr)


def pick_value(text: str, col_type: str) -> Any:
    """Преобразует введённый текст в значение нужного типа."""
    while True:
        raw = ask(text)
        try:
            return S.coerce_nullable(raw, col_type)
        except S.SqlBuildError as exc:
            print(f"Некорректное значение: {exc}", file=sys.stderr)


def parse_pairs(columns: dict[str, str], text: str) -> dict[str, Any]:
    """Разбирает строку вида 'price=100, stock=5' в словарь присваиваний."""
    result: dict[str, Any] = {}
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise S.SqlBuildError(f"фрагмент «{piece}» не вида колонка=значение")
        col, value = piece.split("=", 1)
        col = col.strip()
        if col not in columns:
            raise S.SqlBuildError(f"колонки «{col}» нет в таблице")
        result[col] = S.coerce_nullable(value.strip(), columns[col])
    if not result:
        raise S.SqlBuildError("не задано ни одного поля")
    return result


def collect_filters(columns: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    """Собирает список условий WHERE. Возвращает (фильтры, логика)."""
    print("\nВыберите колонки для фильтрации (номер из списка или имя):")
    for i, (name, ctype) in enumerate(columns.items(), 1):
        print(f"  {i}) {name}  ({ctype})")
    print("Операторы: = <> > >= < <= IN")
    filters: list[dict[str, Any]] = []
    while True:
        hint = " (пусто — закончить)" if filters else ""
        col = pick_column(columns, f"Фильтр {len(filters) + 1}: колонка{hint}",
                          allow_blank=bool(filters))
        if col == "":
            break
        op = ask("Оператор", "=") or "="
        op_norm = op.strip().upper()
        if not S.is_valid_operator(op_norm):
            print("Некорректный оператор. Разрешены: = <> > >= < <= IN",
                  file=sys.stderr)
            continue
        if op_norm in S.SPECIAL_OPERATORS:
            raw = ask("Значения через запятую")
            values = []
            for piece in raw.split(","):
                piece = piece.strip()
                if piece:
                    values.append(S.coerce_nullable(piece, columns[col]))
            value: Any = values
        else:
            value = pick_value("Значение", columns[col])
        filters.append({"column": col, "op": op_norm, "value": value})
        if not ask_yes("Добавить ещё условие?", default_no=True):
            break
    logical = "AND"
    if len(filters) > 1:
        logical = ask("Логика объединения условий (AND/OR)", "AND").upper()
        if logical not in S.LOGICAL_OPERATORS:
            logical = "AND"
    return filters, logical


# --------------------------------------------------------------------------- #
# Печать и выполнение
# --------------------------------------------------------------------------- #

def show(log: Logger, conn: psycopg.Connection, query: S.sql.Composable,
         title: str, limit: int | None = None) -> None:
    """Печатает запрос, выполняет его и выводит результат."""
    text = query.as_string(conn)
    print(f"\n-- {title}\n-- SQL: {text}")
    log.raw(f"SQL: {text}")
    try:
        headers, rows = dbmod.run_select(conn, query)
    except Exception as exc:                      # noqa: BLE001 — переводим в дружелюбный текст
        log.error(log.db_error_text(exc, "выполнить запрос на чтение"))
        return
    if limit is not None and len(rows) > limit:
        rows = rows[:limit]
    print(dbmod.format_table(headers, rows))
    log.info(f"Запрос выполнен успешно. Получено строк: {len(rows)}.")


def execute_write(log: Logger, conn: psycopg.Connection, query: S.sql.Composable,
                  title: str, action: str, returning: bool = False) -> Any:
    """Выполняет INSERT/UPDATE и печатает результат."""
    text = query.as_string(conn)
    print(f"\n-- {title}\n-- SQL: {text}")
    log.raw(f"SQL: {text}")
    try:
        if returning:
            new_id = dbmod.run_write_returning(conn, query)
            log.info(f"{action} выполнено успешно. Новый идентификатор: {new_id}")
            return new_id
        affected = dbmod.run_write(conn, query)
        log.info(f"{action} выполнено успешно. Затронуто строк: {affected}")
        return affected
    except Exception as exc:                      # noqa: BLE001
        conn.rollback()
        log.error(log.db_error_text(exc, action))
        return None


# --------------------------------------------------------------------------- #
# Действия 6.1–6.4
# --------------------------------------------------------------------------- #

def action_select_all(log: Logger, conn: psycopg.Connection, table: str,
                      columns: dict[str, str]) -> None:
    query = S.build_select(table, list(columns))
    show(log, conn, query, f"6.1 Все строки таблицы {table}", None)


def action_select_one(log: Logger, conn: psycopg.Connection, table: str,
                      columns: dict[str, str]) -> None:
    filters, logical = collect_filters(columns)
    if not filters:
        return
    limit = ask("Максимум строк в выводе", "50")
    limit = int(limit) if str(limit).isdigit() else None
    query = S.build_select(table, list(columns), filters, logical, limit)
    show(log, conn, query, f"6.2 Фильтрация таблицы {table}", limit)


def action_select_many(log: Logger, conn: psycopg.Connection, table: str,
                       columns: dict[str, str]) -> None:
    action_select_one(log, conn, table, columns)   # тот же сборщик, ≥2 условий


def action_update_one(log: Logger, conn: psycopg.Connection, table: str,
                      columns: dict[str, str]) -> None:
    pk = dbmod.primary_key(conn, table) or "id"
    editable = {k: v for k, v in columns.items() if k != pk}
    print(f"\nДоступно для изменения (без «{pk}»): "
          + ", ".join(editable))
    print('Формат: колонка=значение, колонка=значение. Пример: price=19990, stock=3')
    try:
        assignments = parse_pairs(editable, ask("Что изменить"))
    except S.SqlBuildError as exc:
        log.error(str(exc))
        return
    value = pick_value(f"Значение «{pk}» записи, которую меняем", columns[pk])
    query = S.build_update(table, assignments,
                           [{"column": pk, "op": "=", "value": value}])
    execute_write(log, conn, query, "6.4 Обновление одной записи",
                  "Обновление записи")


def action_update_many(log: Logger, conn: psycopg.Connection, table: str,
                       columns: dict[str, str]) -> None:
    pk = dbmod.primary_key(conn, table) or "id"
    editable = {k: v for k, v in columns.items() if k != pk}
    print("\nОбщее новое значение для нескольких записей.")
    set_col = pick_column(editable, "Какую колонку меняем")
    set_value = pick_value(f"Новое значение для {set_col}", editable[set_col])
    where_col = pick_column(columns, "Колонка, по которой выбираем записи")
    raw = ask(f"Значения «{where_col}» через запятую (IN)")
    values = [S.coerce_nullable(p.strip(), columns[where_col])
              for p in raw.split(",") if p.strip()]
    if not values:
        log.error("не задано ни одного значения для IN")
        return
    query = S.build_update(table, {set_col: set_value},
                           [{"column": where_col, "op": "IN", "value": values}])
    execute_write(log, conn, query, "6.4 Обновление нескольких записей",
                  "Массовое обновление")


def action_insert_one(log: Logger, conn: psycopg.Connection, table: str,
                      columns: dict[str, str]) -> None:
    values = insert_row_values(log, table, columns)
    if values is None:
        return
    query = S.build_insert(table, list(values), [list(values.values())])
    execute_write(log, conn, query, "6.4 Вставка одной строки",
                  "Вставка строки", returning=True)


def insert_row_values(log: Logger, table: str,
                      columns: dict[str, str]) -> dict[str, Any] | None:
    """Спрашивает значения всех колонок. Пустой ввод -> колонка пропускается."""
    print(f"\nВставка в «{table}». Пустой ввод — оставить значение по умолчанию.")
    values: dict[str, Any] = {}
    for name, ctype in columns.items():
        raw = ask(f"  {name} ({ctype}) — Enter, чтобы пропустить", "")
        if raw == "":
            continue
        try:
            values[name] = S.coerce_nullable(raw, ctype)
        except S.SqlBuildError as exc:
            log.error(f"колонка {name}: {exc}")
            return None
    if not values:
        log.error("не заполнено ни одного поля — вставка отменена")
        return None
    return values


def action_insert_many(log: Logger, conn: psycopg.Connection, table: str,
                       columns: dict[str, str]) -> None:
    print(f"\nВставка нескольких строк в «{table}». Пустая строка — конец ввода.")
    cols: list[str] | None = None
    rows: list[list[Any]] = []
    while True:
        raw = ask(f"Строка {len(rows) + 1}: значения через ';' или Enter для конца", "")
        if raw == "":
            break
        if cols is None:
            print("Укажите колонки в том же порядке (номера через запятую или имена).")
            for i, (n, t) in enumerate(columns.items(), 1):
                print(f"  {i}) {n} ({t})")
            head = ask("Колонки")
            chosen = []
            for piece in head.replace(";", ",").split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if piece.isdigit() and 1 <= int(piece) <= len(columns):
                    chosen.append(list(columns)[int(piece) - 1])
                elif piece in columns:
                    chosen.append(piece)
                else:
                    log.error(f"нет колонки «{piece}»")
                    return
            cols = chosen
        parts = [p.strip() for p in raw.split(";")]
        if len(parts) != len(cols):
            log.error(f"нужно {len(cols)} значений (по числу колонок)")
            continue
        try:
            rows.append([S.coerce_nullable(p, columns[c]) for p, c in zip(parts, cols)])
        except S.SqlBuildError as exc:
            log.error(str(exc))
    if not rows or cols is None:
        log.error("не задано ни одной строки — вставка отменена")
        return
    query = S.build_insert(table, cols, rows, returning=None)
    execute_write(log, conn, query, f"6.4 Вставка {len(rows)} строк",
                  "Многорядная вставка")


def action_insert_linked(log: Logger, conn: psycopg.Connection, table: str,
                         columns: dict[str, str]) -> None:
    """Вставка в две связанные таблицы: родитель -> RETURNING id -> потомок."""
    fks = foreign_keys(conn, table)
    if not fks:
        log.error(
            f"таблица «{table}» не является родительской ни для одной таблицы "
            "(нет таблиц со ссылкой на неё)"
        )
        return
    print(f"\nТаблица «{table}» — родительская. Связанные таблицы:")
    child_names = list(fks)
    for i, name in enumerate(child_names, 1):
        print(f"  {i}) {name}  (ссылка {fks[name]}.{fk_column(conn, name, table)})")
    child = pick_column({n: "" for n in child_names}, "Дочерняя таблица")
    fk_col = fk_column(conn, child, table)
    pk = dbmod.primary_key(conn, table) or "id"

    parent_values = insert_row_values(log, table, columns)
    if parent_values is None:
        return
    q_parent = S.build_insert(table, list(parent_values),
                              [list(parent_values.values())], returning=pk)
    text = q_parent.as_string(conn)
    print(f"\n-- 6.4 Вставка в связанные таблицы (шаг 1: {table})")
    print(f"-- SQL: {text}")
    log.raw(f"SQL: {text}")

    child_columns = dbmod.table_columns(conn, child)
    child_pk = dbmod.primary_key(conn, child)
    editable_child = {k: v for k, v in child_columns.items()
                      if k not in (fk_col, child_pk)}
    print(f"\nШаг 2: «{child}». Колонка {fk_col} подставляется автоматически "
          f"из шага 1. Пустой ввод — пропустить поле.")
    child_values: dict[str, Any] = {}
    for name, ctype in editable_child.items():
        raw = ask(f"  {name} ({ctype}) — Enter, чтобы пропустить", "")
        if raw == "":
            continue
        try:
            child_values[name] = S.coerce_nullable(raw, ctype)
        except S.SqlBuildError as exc:
            log.error(f"колонка {name}: {exc}")
            return

    # Обе вставки — в одной транзакции, чтобы не осталось «висячего» родителя.
    try:
        new_id = dbmod.run_write_returning(conn, q_parent)
        log.info(f"Шаг 1 выполнен. Новый идентификатор {table}: {new_id}")
        child_values[fk_col] = new_id
        q_child = S.build_insert(child, list(child_values),
                                [list(child_values.values())],
                                returning=child_pk)
        text2 = q_child.as_string(conn)
        print(f"-- SQL: {text2}")
        log.raw(f"SQL: {text2}")
        dbmod.run_write_returning(conn, q_child)
        log.info(f"Шаг 2 выполнен. Строка добавлена в «{child}».")
    except Exception as exc:                      # noqa: BLE001
        conn.rollback()
        log.error(log.db_error_text(exc, "выполнить вставку в связанные таблицы"))


# --------------------------------------------------------------------------- #
# Справочники по связям
# --------------------------------------------------------------------------- #

def _fk_rows(conn: psycopg.Connection, parent: str | None = None,
             child: str | None = None) -> list[tuple[str, str, str, str]]:
    """Список внешних ключей: (дочерняя, колонка-потомок, родитель, колонка-родитель).

    Используется pg_catalog, а не information_schema: последняя показывает
    только те ограничения, на которые у текущей роли есть права, поэтому у
    непривилегированного app_user связи «терялись».
    """
    cond = ["c.contype = 'f'", "cn.nspname = %s", "pn.nspname = %s"]
    params: list[Any] = [dbmod.SCHEMA, dbmod.SCHEMA]
    if parent is not None:
        cond.append("pc.relname = %s")
        params.append(parent)
    if child is not None:
        cond.append("cc.relname = %s")
        params.append(child)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT cc.relname, ca.attname, pc.relname, pa.attname
            FROM pg_constraint c
            JOIN pg_class cc      ON cc.oid = c.conrelid
            JOIN pg_namespace cn  ON cn.oid = cc.relnamespace
            JOIN pg_class pc      ON pc.oid = c.confrelid
            JOIN pg_namespace pn  ON pn.oid = pc.relnamespace
            JOIN LATERAL unnest(c.conkey, c.confkey) AS k(child_att, parent_att)
              ON true
            JOIN pg_attribute ca  ON ca.attrelid = c.conrelid
                                 AND ca.attnum = k.child_att
            JOIN pg_attribute pa  ON pa.attrelid = c.confrelid
                                 AND pa.attnum = k.parent_att
            WHERE {' AND '.join(cond)}
            ORDER BY cc.relname, ca.attname
            """,
            tuple(params),
        )
        return [tuple(r) for r in cur.fetchall()]


def foreign_keys(conn: psycopg.Connection, table: str) -> dict[str, str]:
    """{дочерняя таблица: колонка-ссылка} для ссылок на указанную таблицу."""
    return {child: child_col for child, child_col, _p, _pc
            in _fk_rows(conn, parent=table)}


def fk_column(conn: psycopg.Connection, child: str, parent: str) -> str:
    """Возвращает колонку дочерней таблицы, ссылающуюся на родительскую."""
    for c, child_col, p, _pc in _fk_rows(conn, parent=parent, child=child):
        if p == parent:
            return child_col
    return "id"


def show_structure(log: Logger, conn: psycopg.Connection) -> None:
    """Печатает список таблиц, колонок и связей (удобно на защите)."""
    print("\n=== Структура БД (схема public) ===")
    for t in dbmod.list_tables(conn):
        cols = dbmod.table_columns(conn, t)
        print(f"\n{t}  ({len(cols)} колонок)")
        for name, ctype in cols.items():
            print(f"    {name:<14} {ctype}")
        for child, _ in foreign_keys(conn, t).items():
            print(f"    -> ссылается таблица: {child}.{fk_column(conn, child, t)}")
    log.info("Структура БД выведена на экран.")


# --------------------------------------------------------------------------- #
# Основной цикл
# --------------------------------------------------------------------------- #

def choose_table(log: Logger, conn: psycopg.Connection) -> str | None:
    tables = dbmod.list_tables(conn)
    if not tables:
        log.error("в базе нет ни одной таблицы — выполните sql/01_schema.sql")
        return None
    print("\n=== Доступные таблицы ===")
    for i, t in enumerate(tables, 1):
        print(f"  {i}) {t}")
    return pick_column({t: "" for t in tables}, "Таблица (номер или имя)")


ACTIONS = {
    "1": action_select_all,
    "2": action_select_one,
    "3": action_select_many,
    "4": action_update_one,
    "5": action_update_many,
    "6": action_insert_one,
    "7": action_insert_many,
    "8": action_insert_linked,
}


def repl(log: Logger, conn: psycopg.Connection, user: str) -> None:
    table = choose_table(log, conn)
    if table is None:
        return
    columns = dbmod.table_columns(conn, table)
    while True:
        print(MENU.replace("\\a", "").format(table=table, user=user))
        try:
            choice = input().strip()
        except EOFError:
            print("Завершение работы.")
            return
        if choice.lower() in ("q", "й", "exit", "quit"):
            return
        if choice == "0":
            table = choose_table(log, conn) or table
            columns = dbmod.table_columns(conn, table)
            continue
        if choice == "9":
            show_structure(log, conn)
            continue
        action = ACTIONS.get(choice)
        if action is None:
            print("Неизвестный пункт меню.", file=sys.stderr)
            continue
        try:
            action(log, conn, table, columns)
        except S.SqlBuildError as exc:
            log.error(str(exc))
        except Quit:
            return
        except psycopg.Error as exc:
            conn.rollback()
            log.error(log.db_error_text(exc, "выполнить операцию"))
        except (ValueError, TypeError) as exc:
            log.raw(f"value error: {exc}")
            log.error("введено некорректное значение, повторите ввод")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CRUD-приложение (просмотр/обновление/вставка) над БД "
                    "«склад интернет-магазина»."
    )
    parser.add_argument("--config", default=os.environ.get("APP_CONFIG", "config/app.conf"),
                        help="путь к конфиг-файлу подключения (TOML)")
    parser.add_argument("--user", default="", help="логин БД (иначе — запрос в консоли)")
    parser.add_argument("--password", default="", help="пароль БД (иначе — запрос в консоли)")
    parser.add_argument("--log-file", default="",
                        help="файл дублирующего технического журнала; "
                             "то же самое можно задать переменной окружения "
                             "APP_LOG_FILE")
    args = parser.parse_args(argv)

    log = Logger(args.log_file or None)
    log.raw(f"start: argv={list(argv) if argv else sys.argv[1:]}")

    try:
        cfg = dbmod.load_config(args.config)
    except (ValueError, OSError) as exc:
        log.error(f"не удалось прочитать настройки подключения: {exc}")
        return 2

    username = args.user or os.environ.get("PGUSER", "")
    if not username:
        try:
            username = input("Логин БД: ").strip()
        except EOFError:
            log.error("логин не введён")
            return 2
    try:
        username = dbmod.validate_username(username)
    except ValueError as exc:
        log.error(str(exc))
        return 2

    password = args.password or os.environ.get("PGPASSWORD", "")
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass("Пароль БД: ")
        else:
            try:
                password = input("Пароль БД: ")
            except EOFError:
                log.error("пароль не введён")
                return 2

    try:
        conn = dbmod.connect(cfg, username, password)
    except Exception as exc:                      # noqa: BLE001
        log.error(log.db_error_text(exc, "подключиться к базе данных"))
        return 1

    log.info(
        f"Подключение к базе выполнено успешно "
        f"(пользователь {username}, сервер {cfg.host}:{cfg.port})."
    )
    try:
        repl(log, conn, username)
    except Quit:
        pass
    except KeyboardInterrupt:
        log.info("Работа прервана пользователем.")
    except Exception as exc:                      # noqa: BLE001
        log.error(log.db_error_text(exc, "продолжить работу"))
        return 1
    finally:
        try:
            conn.close()
        except Exception:                         # noqa: BLE001
            pass
        log.raw("connection closed")
    log.info("Работа приложения завершена.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
