#!/usr/bin/env python3
"""Задание №5 — чек-лист обеспечения безопасности приложения.

Источники:
  * приложение из задания №3 (репозиторий postgres-crud-app);
  * модель угроз из задания №4 (hw4/model/report-data.json, 15 угроз STRIDE + DREAD).

Генератор создаёт:
  * hw5/checklist.csv        — машиночитаемая таблица;
  * hw5/hw5-otchet.docx      — отчёт с таблицами;
  * hw5/README.md            — краткое описание и таблица в Markdown.

Запуск:
    python hw5/tools/make_checklist.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parents[2]
HW5 = ROOT / "hw5"
REPORT_DATA = ROOT / "hw4" / "model" / "report-data.json"

# --------------------------------------------------------------------------- #
# Чек-лист
# --------------------------------------------------------------------------- #

ITEMS = [
    {
        "id": "Ч-01",
        "text": "Приложение должно формировать SQL-запросы только из белого списка "
                "идентификаторов (таблицы, колонки, операторы) и передавать все значения "
                "параметрами драйвера; конкатенация пользовательского ввода с текстом "
                "запроса должна отсутствовать.",
        "threats": ["T-04"],
        "standards": "OWASP ASVS 4.0.3, гл. V5 «Validation, Sanitization and Encoding»; "
                     "CWE-89; OWASP Top 10 2021 A03 «Injection»; "
                     "ГОСТ Р 56939-2016; БДУ ФСТЭК УБИ.006",
        "check": "pytest tests/test_sqlsafe.py (набор инъекций); просмотр "
                 "src/sqlsafe.py: сборка через sql.Identifier / sql.Literal",
        "status": "выполнено",
        "artifact": "src/sqlsafe.py, tests/test_sqlsafe.py",
    },
    {
        "id": "Ч-02",
        "text": "Приложение должно подключаться к СУБД только под непривилегированной "
                "ролью с минимально необходимыми правами; административная роль не должна "
                "использоваться для работы приложения.",
        "threats": ["T-05", "T-15"],
        "standards": "OWASP ASVS 4.0.3, гл. V1 «Architecture» и V4 «Access Control»; "
                     "CWE-250, CWE-269; приказ ФСТЭК России № 17 (2013), требования "
                     "к управлению доступом; БДУ ФСТЭК УБИ.122",
        "check": "sql/01_schema.sql: CREATE ROLE app_user NOSUPERUSER NOCREATEDB "
                 "NOCREATEROLE NOINHERIT; REVOKE DELETE; docker/init.sql",
        "status": "выполнено",
        "artifact": "sql/01_schema.sql, docker/init.sql",
    },
    {
        "id": "Ч-03",
        "text": "СУБД должна быть доступна только из внутренней сети контейнеров; публикация "
                "порта 5432 на хост не должна использоваться в штатной конфигурации.",
        "threats": ["T-05", "T-13"],
        "standards": "OWASP ASVS 4.0.3, гл. V14 «Configuration»; CWE-668; "
                     "OWASP Top 10 2021 A05 «Security Misconfiguration»; "
                     "ГОСТ Р 56939-2016",
        "check": "docker compose config: секция ports у сервиса db; "
                 "ss -ltn на хосте после запуска",
        "status": "не выполнено",
        "artifact": "docker-compose.yml (ports: 5432:5432)",
    },
    {
        "id": "Ч-04",
        "text": "Пароли и иные секреты не должны храниться в репозитории и образе в открытом "
                "виде; в репозитории допускаются только шаблоны без значений, а рабочие "
                "секреты должны передаваться через окружение или хранилище секретов.",
        "threats": ["T-03", "T-06"],
        "standards": "OWASP ASVS 4.0.3, гл. V6 «Stored Cryptography» и V14 «Configuration»; "
                     "CWE-798, CWE-522; OWASP Top 10 2021 A02 «Cryptographic Failures», "
                     "A05 «Security Misconfiguration»; ГОСТ Р 56939-2016",
        "check": "git ls-files | grep -E 'env$|conf$'; grep -rn 'password\\|secret' "
                 "docker-compose.yml crud.env sql/",
        "status": "не выполнено",
        "artifact": "crud.env, docker-compose.yml, sql/01_schema.sql",
    },
    {
        "id": "Ч-05",
        "text": "Соединение приложения с СУБД должно быть защищено: sslmode не ниже "
                "require, а при проверке сертификата — verify-ca или verify-full; "
                "режим prefer не должен использоваться для передачи учётных данных.",
        "threats": ["T-06", "T-10"],
        "standards": "OWASP ASVS 4.0.3, гл. V9 «Communications»; CWE-319, CWE-295; "
                     "OWASP Top 10 2021 A02 «Cryptographic Failures»; "
                     "ГОСТ Р 56939-2016",
        "check": "конфиг app.docker.conf: sslmode; db.py: белый список значений sslmode",
        "status": "не выполнено",
        "artifact": "config/app.docker.conf, src/db.py",
    },
    {
        "id": "Ч-06",
        "text": "Учётные данные должны передаваться драйверу отдельными аргументами и "
                "никогда не попадать в журнал, сообщения об ошибках, историю команд или "
                "текст SQL-запроса.",
        "threats": ["T-01", "T-09"],
        "standards": "OWASP ASVS 4.0.3, гл. V2 «Authentication» и V7 «Error Handling and "
                     "Logging»; CWE-532, CWE-522; OWASP Top 10 2021 A09 «Security Logging "
                     "and Monitoring Failures»; ГОСТ Р 56939-2016",
        "check": "src/db.py: connect(user=..., password=...) без склейки DSN; "
                 "поиск пароля в логах: grep -i password logs/",
        "status": "выполнено",
        "artifact": "src/db.py, src/logger.py",
    },
    {
        "id": "Ч-07",
        "text": "Журнал должен фиксировать, какая учётная запись выполнила изменение и какие "
                "строки были затронуты; файл журнала должен быть защищён от изменения и "
                "удаления пользователем приложения.",
        "threats": ["T-07", "T-08"],
        "standards": "OWASP ASVS 4.0.3, гл. V7 «Error Handling and Logging»; "
                     "CWE-778, CWE-732; OWASP Top 10 2021 A09; приказ ФСТЭК России № 17 "
                     "(2013), требования к регистрации событий безопасности",
        "check": "src/logger.py: состав записей; docker-compose.yml: том ./logs "
                 "(права записи); сравнение записей до/после UPDATE",
        "status": "частично",
        "artifact": "src/logger.py, docker-compose.yml",
    },
    {
        "id": "Ч-08",
        "text": "Сообщения об ошибках, выдаваемые пользователю, не должны содержать SQLSTATE, "
                "тексты драйвера, SQL-запрос и трассировку; подробный технический журнал "
                "должен писаться только в защищённый файл.",
        "threats": ["T-09"],
        "standards": "OWASP ASVS 4.0.3, гл. V7 «Error Handling and Logging»; "
                     "CWE-209; OWASP Top 10 2021 A09; ГОСТ Р 56939-2016",
        "check": "src/logger.py: friendly-сообщения; провокация ошибки подключения и "
                 "просмотр stderr",
        "status": "выполнено",
        "artifact": "src/logger.py",
    },
    {
        "id": "Ч-09",
        "text": "Размер пользовательского ввода должен быть ограничен (число условий IN, "
                "длина значений, число строк); на стороне СУБД должны быть заданы "
                "statement_timeout и лимит соединений.",
        "threats": ["T-12", "T-13"],
        "standards": "OWASP ASVS 4.0.3, гл. V5 «Validation, Sanitization and Encoding»; "
                     "CWE-400, CWE-770; OWASP Top 10 2021 A04 «Insecure Design»; "
                     "ГОСТ Р 56939-2016",
        "check": "src/app.py: limit строк вывода; отсутствие лимита на число фильтров; "
                 "docker/postgresql.conf: max_connections, statement_timeout",
        "status": "частично",
        "artifact": "src/app.py, docker/postgresql.conf",
    },
    {
        "id": "Ч-10",
        "text": "Контейнер приложения должен работать от непривилегированного пользователя, "
                "без монтирования docker.sock и с минимальным набором прав на файловую "
                "систему (read-only там, где это возможно).",
        "threats": ["T-14"],
        "standards": "OWASP ASVS 4.0.3, гл. V14 «Configuration»; CWE-250; "
                     "OWASP Top 10 2021 A05 «Security Misconfiguration»; "
                     "ГОСТ Р 56939-2016",
        "check": "Dockerfile: USER appuser; docker compose config: volumes, "
                 "security_opt, read_only",
        "status": "частично",
        "artifact": "Dockerfile, docker-compose.yml",
    },
    {
        "id": "Ч-11",
        "text": "Взаимодействие контейнеров должно быть аутентифицировано и, при передаче "
                "учётных данных, зашифровано; подключение к СУБД должно проверять "
                "подлинность сервера.",
        "threats": ["T-02", "T-10"],
        "standards": "OWASP ASVS 4.0.3, гл. V9 «Communications»; CWE-306, CWE-319; "
                     "OWASP Top 10 2021 A02, A07 «Identification and Authentication "
                     "Failures»; ГОСТ Р 56939-2016",
        "check": "docker-compose.yml: сеть, отсутствие mTLS; sslmode в конфиге; "
                 "проверка сертификата сервера",
        "status": "не выполнено",
        "artifact": "docker-compose.yml, config/app.docker.conf",
    },
    {
        "id": "Ч-12",
        "text": "Роль приложения должна иметь доступ только к необходимым таблицам и "
                "операциям; любое расширение прав должно выполняться отдельной миграцией "
                "с проверкой и фиксироваться в журнале изменений.",
        "threats": ["T-11", "T-15"],
        "standards": "OWASP ASVS 4.0.3, гл. V4 «Access Control»; CWE-732, CWE-269; "
                     "OWASP Top 10 2021 A01 «Broken Access Control»; "
                     "приказ ФСТЭК России № 17 (2013), требования к управлению доступом",
        "check": "sql/01_schema.sql: GRANT SELECT, INSERT, UPDATE на 4 таблицы, "
                 "REVOKE DELETE; сверка с фактическими запросами приложения",
        "status": "частично",
        "artifact": "sql/01_schema.sql",
    },
]

STATUS_ORDER = {"выполнено": 0, "частично": 1, "не выполнено": 2}


def load_threats() -> list[dict]:
    data = json.loads(REPORT_DATA.read_text(encoding="utf-8"))
    return sorted(data["threats"], key=lambda t: -t["score"])


def coverage_rows(threats: list[dict]) -> list[tuple[str, str, str, str]]:
    rows = []
    for t in threats:
        covered = [i["id"] for i in ITEMS if t["id"] in i["threats"]]
        rows.append((t["id"], t["title"], f"{t['score']:g}", ", ".join(covered) or "—"))
    return rows


# --------------------------------------------------------------------------- #
# Вывод
# --------------------------------------------------------------------------- #

def write_csv() -> None:
    path = HW5 / "checklist.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["№", "Пункт чек-листа", "Угрозы (задание №4)",
                         "Стандарты и разделы", "Как проверяется", "Статус в ПЗ №3"])
        for i in ITEMS:
            writer.writerow([i["id"], i["text"], ", ".join(i["threats"]),
                             i["standards"], i["check"], i["status"]])
    print("written", path)


def write_markdown(threats: list[dict]) -> None:
    lines = [
        "# Задание №5 — чек-лист обеспечения безопасности приложения",
        "",
        "Объект: CRUD-приложение из задания №3 (`postgres-crud-app`).",
        "Основание: модель угроз STRIDE + DREAD из задания №4 (15 угроз).",
        "",
        "Стандарты: OWASP ASVS 4.0.3, CWE, OWASP Top 10 2021, ГОСТ Р 56939-2016,",
        "приказ ФСТЭК России № 17 (2013), БДУ ФСТЭК (УБИ.006, УБИ.033, УБИ.122).",
        "",
        "## Чек-лист",
        "",
        "| № | Пункт | Угрозы | Стандарты | Проверка | Статус |",
        "|---|-------|--------|-----------|----------|--------|",
    ]
    for i in ITEMS:
        lines.append(
            f"| {i['id']} | {i['text']} | {', '.join(i['threats'])} | "
            f"{i['standards']} | {i['check']} | {i['status']} |"
        )
    lines += ["", "## Покрытие угроз", "",
              "| Угроза | Название | DREAD | Пункты |", "|---|---|---|---|"]
    for tid, title, score, items in coverage_rows(threats):
        lines.append(f"| {tid} | {title} | {score} | {items} |")
    lines += ["", "Машиночитаемая версия: `hw5/checklist.csv`.",
              "Полный отчёт: `hw5/hw5-otchet.docx`.", ""]
    (HW5 / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print("written", HW5 / "README.md")


def _set_cell(cell, text: str, bold: bool = False, size: int = 9) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)


def write_docx(threats: list[dict]) -> None:
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.left_margin = section.right_margin = Cm(1.2)
    section.top_margin = section.bottom_margin = Cm(1.5)

    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Задание №5\nЧек-лист обеспечения безопасности приложения")
    run.bold = True
    run.font.size = Pt(15)

    doc.add_paragraph(
        "Объект: CRUD-приложение над PostgreSQL «склад интернет-магазина» "
        "(задание №3, репозиторий postgres-crud-app)."
    )
    doc.add_paragraph(
        "Основание: модель угроз STRIDE и оценка рисков DREAD (задание №4), "
        f"{len(threats)} локализованных угроз."
    )

    doc.add_heading("1. Методика и перечень стандартов", level=1)
    doc.add_paragraph(
        "Чек-лист составлен в позитивной формулировке («приложение должно …»). "
        "В него включены только пункты, которые: (а) подтверждаются практикой из задания №3 — "
        "то есть относятся к фактически реализованным механизмам и артефактам приложения; "
        "(б) соответствуют угрозам из модели задания №4, имеющим наибольший риск по DREAD."
    )
    for line in (
        "OWASP Application Security Verification Standard (ASVS), версия 4.0.3 — главы "
        "V1 «Architecture», V2 «Authentication», V4 «Access Control», "
        "V5 «Validation, Sanitization and Encoding», V6 «Stored Cryptography», "
        "V7 «Error Handling and Logging», V9 «Communications», V14 «Configuration».",
        "MITRE CWE — классы дефектов: CWE-89, CWE-209, CWE-250, CWE-269, CWE-295, "
        "CWE-306, CWE-319, CWE-400, CWE-522, CWE-532, CWE-668, CWE-732, CWE-770, CWE-778, CWE-798.",
        "OWASP Top 10 (2021): A01, A02, A03, A04, A05, A07, A09.",
        "ГОСТ Р 56939-2016 «Защита информации. Разработка безопасного программного "
        "обеспечения. Общие требования».",
        "Приказ ФСТЭК России от 11.02.2013 № 17 — требования к управлению доступом "
        "и регистрации событий безопасности.",
        "Банк данных угроз безопасности информации ФСТЭК России: УБИ.006, УБИ.033, УБИ.122.",
    ):
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("2. Чек-лист обеспечения безопасности приложения", level=1)
    headers = ["№", "Пункт чек-листа", "Угрозы", "Стандарты и разделы", "Как проверяется", "Статус"]
    widths = [Cm(1.4), Cm(8.4), Cm(2.2), Cm(7.4), Cm(6.2), Cm(2.2)]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for idx, h in enumerate(headers):
        _set_cell(table.rows[0].cells[idx], h, bold=True)
    for item in ITEMS:
        cells = table.add_row().cells
        values = [item["id"], item["text"], ", ".join(item["threats"]),
                  item["standards"], item["check"], item["status"]]
        for idx, value in enumerate(values):
            _set_cell(cells[idx], value)
    for row in table.rows:
        for idx, width in enumerate(widths):
            row.cells[idx].width = width

    doc.add_heading("3. Покрытие угроз модели задания №4", level=1)
    doc.add_paragraph(
        "Все 15 угроз модели сопоставлены пунктам чек-листа. Риск приведён по шкале DREAD "
        "из отчёта задания №4; пункты чек-листа упорядочены по убыванию риска угроз."
    )
    cov = doc.add_table(rows=1, cols=4)
    cov.style = "Table Grid"
    for idx, h in enumerate(["Угроза", "Название", "DREAD", "Пункты чек-листа"]):
        _set_cell(cov.rows[0].cells[idx], h, bold=True)
    for tid, title, score, items in coverage_rows(threats):
        cells = cov.add_row().cells
        for idx, value in enumerate([tid, title, score, items]):
            _set_cell(cells[idx], value)
    for row in cov.rows:
        for idx, width in enumerate([Cm(2.0), Cm(13.0), Cm(2.0), Cm(10.0)]):
            row.cells[idx].width = width

    doc.add_heading("4. Ответы на вопросы преподавателя", level=1)
    qa = [
        ("Почему в чек-листе 12 пунктов, а угроз 15?",
         "Пункт чек-листа — это мера, а не угроза. Одна мера закрывает несколько угроз "
         "(например, Ч-04 закрывает T-03 и T-06), поэтому пунктов меньше. Таблица раздела 3 "
         "показывает, что ни одна из 15 угроз не осталась без меры."),
        ("Почему формулировки позитивные?",
         "Это требование задания: «в приложении должно обеспечиваться …». Позитивная "
         "формулировка задаёт проверяемое свойство, а не запрет, и допускает автоматическую "
         "проверку."),
        ("Как связаны DREAD и порядок пунктов?",
         "Пункты упорядочены по максимальному риску угроз, которые они закрывают: сначала "
         "T-04, T-05, T-15 (DREAD 20), затем T-06 (19), T-03 и T-14 (18), далее T-01 и T-02 (17)."),
        ("Почему ASVS версии 4.0.3, а не 5.0?",
         "Версия зафиксирована, чтобы ссылки были однозначными: в ASVS 5.0 изменилась "
         "структура глав. Для целей задания важна прослеживаемость, а не номер версии; "
         "при переходе на 5.0 соответствие переносится по названиям глав."),
        ("Чем CWE отличается от CVE?",
         "CWE — класс дефекта (например, CWE-89 «SQL Injection»), CVE — конкретный "
         "экземпляр уязвимости в конкретном продукте и версии. Чек-лист работает на уровне "
         "классов, поэтому ссылки даны на CWE; CVE уместны в требованиях к зависимостям."),
        ("Почему нет пункта про сканирование зависимостей (SCA)?",
         "В модели угроз задания №4 такой угрозы нет, а задание требует включать только "
         "пункты, соответствующие угрозам из №4. Пункт был бы обоснован при расширении "
         "модели угроз."),
        ("Что означает статус «частично»?",
         "Механизм есть, но не покрывает угрозу полностью. Ч-07: журнал ведётся, но не "
         "фиксирует учётную запись и защищён не полностью. Ч-09: ограничен вывод строк, "
         "но не размер входных фильтров. Ч-10: контейнер работает от непривилегированного "
         "пользователя, но без read-only ФС. Ч-12: права ограничены, но SELECT выдан на все "
         "четыре таблицы."),
        ("Как проверять пункты автоматически?",
         "Ч-01 — `pytest tests/test_sqlsafe.py`; Ч-02, Ч-12 — SQL-запрос к "
         "`information_schema.role_table_grants` и `pg_roles`; Ч-03 — `docker compose config` "
         "и `ss -ltn`; Ч-04 — поиск секретов в `git ls-files`; Ч-05, Ч-11 — конфиг "
         "`sslmode` и `pg_stat_ssl`; Ч-06, Ч-08 — провокация ошибки и анализ stderr; "
         "Ч-07 — сверка записей журнала с выполненными UPDATE; Ч-09 — лимиты в конфиге СУБД; "
         "Ч-10 — `docker inspect` (User, ReadonlyRootfs, CapDrop)."),
        ("Какие пункты закрывают топ-3 по DREAD?",
         "T-04 — Ч-01; T-05 — Ч-02 и Ч-03; T-15 — Ч-02 и Ч-12."),
        ("Что осталось за рамками чек-листа и почему?",
         "Организационные меры (обучение, ревью кода, управление изменениями) и SCA — "
         "они не выводятся из практики задания №3 и не сопоставлены угрозам №4. "
         "Их включение потребовало бы отдельного обоснования."),
    ]
    for question, answer in qa:
        p = doc.add_paragraph()
        run = p.add_run(question)
        run.bold = True
        doc.add_paragraph(answer)

    doc.add_heading("Приложение А. Воспроизведение проверок", level=1)
    for line in (
        "pytest -q tests/ — модульные проверки сборки SQL;",
        "docker compose config — публикация портов, тома, параметры безопасности;",
        "grep -rn \"sslmode\" config/ src/ — фактический режим защиты соединения;",
        "grep -rn \"password\\|secret\" crud.env docker-compose.yml sql/ — поиск секретов;",
        "psql -c \"SELECT rolname, rolsuper, rolcreaterole FROM pg_roles;\" — проверка ролей.",
    ):
        doc.add_paragraph(line, style="List Bullet")

    path = HW5 / "hw5-otchet.docx"
    doc.save(path)
    print("written", path)


def main() -> None:
    HW5.mkdir(exist_ok=True)
    threats = load_threats()
    write_csv()
    write_markdown(threats)
    write_docx(threats)
    done = sum(1 for i in ITEMS if i["status"] == "выполнено")
    partial = sum(1 for i in ITEMS if i["status"] == "частично")
    missing = sum(1 for i in ITEMS if i["status"] == "не выполнено")
    covered = {t for i in ITEMS for t in i["threats"]}
    print(f"items={len(ITEMS)} done={done} partial={partial} missing={missing} "
          f"threats_covered={len(covered)}/{len(threats)}")


if __name__ == "__main__":
    main()
