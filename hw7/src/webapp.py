"""Веб-интерфейс к БД «склад интернет-магазина» (задание №7).

Реализует требования задания:
  * страница входа с логином/паролем пользователя БД;
  * главная страница с переходами к функциям;
  * просмотр таблиц, добавление строк, обновление записей;
  * журналирование неудачных попыток входа;
  * безопасная передача параметров (идентификаторы — по белому списку из
    метаданных, значения — только через параметры драйвера, группы значений —
    через ``= ANY(%s)``).
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any

import psycopg
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from psycopg import sql

# --------------------------------------------------------------------------- #
#  Конфигурация
# --------------------------------------------------------------------------- #
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "appdb")
CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

AUTH_LOG = os.getenv("APP_AUTH_LOG", "logs/auth.log")
APP_LOG = os.getenv("APP_LOG_FILE", "logs/webapp.log")

TABLES = ["supplier", "product", "order", "order_item"]
TABLE_TITLES = {
    "supplier": "Поставщики",
    "product": "Товары",
    "order": "Заказы",
    "order_item": "Позиции заказов",
}
# Таблицы, у которых первичный ключ — id (используется для редактирования).
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PAGE_SIZE = 25

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

# Серверное хранилище учётных данных: в cookie едет только случайный токен,
# пароль БД никогда не попадает в браузер.
SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_TTL = 8 * 3600


def setup_logging() -> None:
    for path in (AUTH_LOG, APP_LOG):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    auth = logging.getLogger("auth")
    auth.setLevel(logging.INFO)
    if not auth.handlers:
        try:
            fh = logging.FileHandler(AUTH_LOG, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            auth.addHandler(fh)
        except PermissionError:
            logging.getLogger(__name__).warning(
                "журнал %s недоступен для записи — события входа идут только в stdout", AUTH_LOG)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        auth.addHandler(sh)


setup_logging()
auth_log = logging.getLogger("auth")


# --------------------------------------------------------------------------- #
#  Сессии и подключение
# --------------------------------------------------------------------------- #
def current_session() -> dict[str, Any] | None:
    sid = session.get("sid")
    if not sid:
        return None
    data = SESSIONS.get(sid)
    if not data:
        return None
    if time.time() - data["created"] > SESSION_TTL:
        SESSIONS.pop(sid, None)
        return None
    return data


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_session() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def get_conn() -> psycopg.Connection:
    data = current_session()
    if data is None:
        abort(401)
    assert data is not None
    conn = psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=data["user"],
        password=data["password"],
        connect_timeout=CONNECT_TIMEOUT,
        application_name="crud-web",
    )
    conn.autocommit = False
    return conn


def close_conn(exc: BaseException | None = None) -> None:
    conn = g.pop("conn", None)
    if conn is None:
        return
    try:
        if exc is None:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()


@app.teardown_appcontext
def _teardown(exc):
    close_conn(exc)


# --------------------------------------------------------------------------- #
#  Метаданные (белый список идентификаторов)
# --------------------------------------------------------------------------- #
def table_meta(conn: psycopg.Connection, table: str) -> dict[str, Any]:
    """Колонки таблицы из information_schema + PK + внешние ключи."""
    if table not in TABLES:
        abort(404)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = %s
             ORDER BY ordinal_position
            """,
            (table,),
        )
        columns = [dict(zip([c.name for c in cur.description], row)) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT a.attname
              FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
             WHERE i.indrelid = %s::regclass AND i.indisprimary
            """,
            (f'public."{table}"',),
        )
        pk = [r[0] for r in cur.fetchall()]

        # pg_catalog, а не information_schema: последняя фильтрует объекты по
        # владельцу, и непривилегированная роль app_user не видит чужие FK.
        cur.execute(
            """
            SELECT a.attname,
                   c.confrelid::regclass::text AS foreign_table,
                   af.attname AS foreign_column
              FROM pg_constraint c
              JOIN pg_attribute a  ON a.attrelid  = c.conrelid  AND a.attnum  = ANY (c.conkey)
              JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = ANY (c.confkey)
             WHERE c.contype = 'f' AND c.conrelid = %s::regclass
            """,
            (f'public."{table}"',),
        )
        fks = {r[0]: {"table": r[1].replace('public.', '').strip('"'),
                      "column": r[2]} for r in cur.fetchall()}

    for col in columns:
        col["is_pk"] = col["column_name"] in pk
        col["fk"] = fks.get(col["column_name"])
        col["readonly"] = bool(col["is_pk"]) or (col["column_default"] is not None
                                                 and col["column_name"] == "created_at")
    return {"name": table, "title": TABLE_TITLES.get(table, table), "columns": columns, "pk": pk}


def column_names(meta: dict[str, Any]) -> set[str]:
    return {c["column_name"] for c in meta["columns"]}


def ident(value: str, allowed: set[str]) -> sql.Identifier:
    if value not in allowed or not IDENT_RE.match(value):
        abort(400, "недопустимое имя колонки")
    return sql.Identifier(value)


def fk_options(conn: psycopg.Connection, table: str, column: str) -> list[tuple[Any, str]]:
    """Варианты для выпадающего списка внешнего ключа."""
    if table not in TABLES or column not in column_names(table_meta(conn, table)):
        abort(400)
    label_col = "name" if table == "supplier" else ("sku" if table == "product" else "customer")
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id, COALESCE({}, id::text) FROM {} ORDER BY id LIMIT 500").format(
                sql.Identifier(label_col), sql.Identifier(table)
            )
        )
        return [(r[0], f"{r[0]} — {r[1]}") for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
#  Маршруты
# --------------------------------------------------------------------------- #
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and current_session() is not None:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        user = (request.form.get("user") or "").strip()
        password = request.form.get("password") or ""
        try:
            conn = psycopg.connect(
                host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                user=user, password=password, connect_timeout=CONNECT_TIMEOUT,
                application_name="crud-web-login",
            )
            conn.close()
        except Exception as exc:  # noqa: BLE001 — любая ошибка = неудачный вход
            auth_log.warning(
                "FAILED LOGIN user=%r ip=%s reason=%s",
                user, request.remote_addr, str(exc).strip().splitlines()[0][:200],
            )
            error = "Неверный логин или пароль (подробности в журнале)."
        else:
            sid = secrets.token_urlsafe(32)
            SESSIONS[sid] = {"user": user, "password": password, "created": time.time()}
            session["sid"] = sid
            auth_log.info("LOGIN OK user=%r ip=%s", user, request.remote_addr)
            nxt = request.args.get("next") or url_for("index")
            if not nxt.startswith("/"):
                nxt = url_for("index")
            return redirect(nxt)

    return render_template("login.html", error=error, db=f"{DB_NAME}@{DB_HOST}:{DB_PORT}")


@app.route("/logout", methods=["POST"])
def logout():
    sid = session.pop("sid", None)
    if sid:
        SESSIONS.pop(sid, None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    data = current_session()
    conn = get_conn()
    g.conn = conn
    counts = {}
    with conn.cursor() as cur:
        for t in TABLES:
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
            counts[t] = cur.fetchone()[0]
    return render_template("index.html", user=data["user"],
                           db=f"{DB_NAME}@{DB_HOST}:{DB_PORT}",
                           counts=counts, titles=TABLE_TITLES, tables=TABLES)


@app.route("/tables")
@login_required
def tables():
    conn = psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=current_session()["user"], password=current_session()["password"],
        connect_timeout=CONNECT_TIMEOUT,
    )
    try:
        counts = {}
        with conn.cursor() as cur:
            for t in TABLES:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
                counts[t] = cur.fetchone()[0]
    finally:
        conn.close()
    return render_template("tables.html", tables=TABLES, titles=TABLE_TITLES, counts=counts)


@app.route("/tables/<table>")
@login_required
def view_table(table: str):
    conn = get_conn()
    g.conn = conn
    meta = table_meta(conn, table)

    ids_raw = request.args.get("ids", "").strip()
    ids: list[int] = []
    page = max(int(request.args.get("page", 1) or 1), 1)
    offset = (page - 1) * PAGE_SIZE

    cols = sql.SQL(", ").join(sql.Identifier(c["column_name"]) for c in meta["columns"])

    if ids_raw:
        # Группа однотипных значений: передаётся ОДНИМ параметром-массивом,
        # без склейки строки запроса.
        try:
            ids = [int(x) for x in re.split(r"[,\s]+", ids_raw) if x]
        except ValueError:
            abort(400, "ids должен содержать целые числа")
        query = sql.SQL("SELECT {} FROM {} WHERE id = ANY(%s) ORDER BY id LIMIT %s OFFSET %s").format(
            cols, sql.Identifier(table)
        )
        params: tuple = (ids, PAGE_SIZE, offset)
    else:
        query = sql.SQL("SELECT {} FROM {} ORDER BY id LIMIT %s OFFSET %s").format(
            cols, sql.Identifier(table)
        )
        params = (PAGE_SIZE, offset)

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return render_template(
        "table.html", meta=meta, rows=rows, page=page, ids=ids_raw,
        next_page=page + 1, has_next=len(rows) == PAGE_SIZE,
    )


@app.route("/add/<table>", methods=["GET", "POST"])
@login_required
def add_row(table: str):
    conn = get_conn()
    g.conn = conn
    meta = table_meta(conn, table)
    editable = [c for c in meta["columns"] if not c["readonly"]]
    options = {
        c["column_name"]: fk_options(conn, c["fk"]["table"], c["fk"]["column"])
        for c in editable if c["fk"]
    }

    if request.method == "POST":
        cols, vals = [], []
        for c in editable:
            name = c["column_name"]
            raw = request.form.get(name, "")
            if raw == "" and c["is_nullable"] == "YES":
                continue
            if raw == "" and c["column_default"] is not None:
                continue
            cols.append(ident(name, column_names(meta)))
            vals.append(raw)
        if not cols:
            flash("Заполните хотя бы одно поле", "error")
        else:
            query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
                sql.Identifier(table),
                sql.SQL(", ").join(cols),
                sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(query, vals)
                    new_id = cur.fetchone()[0]
                conn.commit()
                flash(f"Строка добавлена, id={new_id}", "ok")
                return redirect(url_for("view_table", table=table))
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                flash(f"Ошибка вставки: {str(exc).splitlines()[0]}", "error")

    return render_template("form.html", meta=meta, editable=editable, options=options,
                           action="add", row=None)


@app.route("/edit")
@login_required
def edit_choose():
    return render_template("tables.html", tables=TABLES, titles=TABLE_TITLES, counts=None,
                           edit_mode=True)


@app.route("/edit/<table>/<int:row_id>", methods=["GET", "POST"])
@login_required
def edit_row(table: str, row_id: int):
    conn = get_conn()
    g.conn = conn
    meta = table_meta(conn, table)
    editable = [c for c in meta["columns"] if not c["readonly"]]
    options = {
        c["column_name"]: fk_options(conn, c["fk"]["table"], c["fk"]["column"])
        for c in editable if c["fk"]
    }

    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table)), (row_id,))
        fetched = cur.fetchone()
        names = [d.name for d in cur.description]
    if fetched is None:
        abort(404)
    row = dict(zip(names, fetched))

    if request.method == "POST":
        cols, vals = [], []
        for c in editable:
            name = c["column_name"]
            if name not in request.form:
                continue
            cols.append(ident(name, column_names(meta)))
            vals.append(request.form.get(name))
        if cols:
            query = sql.SQL("UPDATE {} SET {} WHERE id = %s").format(
                sql.Identifier(table),
                sql.SQL(", ").join(
                    sql.SQL("{} = {}").format(col, sql.Placeholder()) for col in cols
                ),
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(query, (*vals, row_id))
                conn.commit()
                flash("Изменения сохранены", "ok")
                return redirect(url_for("view_table", table=table))
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                flash(f"Ошибка обновления: {str(exc).splitlines()[0]}", "error")

    return render_template("form.html", meta=meta, editable=editable, options=options,
                           action="edit", row=row)


@app.route("/healthz")
def healthz():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
