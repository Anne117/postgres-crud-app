"""Журналирование и «дружелюбные» сообщения об ошибках.

Требования задания:
  * факт успешного/неуспешного подключения к БД и выполнения запроса пишется
    в stdout (успех) / stderr (ошибка);
  * текст ошибки дружелюбен к пользователю и НЕ раскрывает деталей реализации
    (ошибки драйвера, СУБД, трассировки, SQLSTATE, тексты запросов);
  * через переменную окружения можно дополнительно передать путь к файлу,
    куда логи дублируются «в чистом виде, без дополнительной обработки»
    (то есть подробный технический лог).
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import traceback
from pathlib import Path

import psycopg

# Имя переменной окружения с путём к файлу дублирующего (технического) лога.
ENV_LOG_FILE = "APP_LOG_FILE"
# Максимальная длина технической строки в файле-дубле.
RAW_LINE_LIMIT = 4000


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Logger:
    """Пишет дружелюбные сообщения в stdout/stderr и «сырой» лог в файл."""

    def __init__(self, log_file: str | os.PathLike[str] | None = None) -> None:
        path = log_file or os.environ.get(ENV_LOG_FILE) or ""
        self.log_file: Path | None = Path(path).expanduser() if path.strip() else None
        self._file_error_reported = False

    # ---------------- вывод пользователю ---------------- #

    def info(self, message: str) -> None:
        """Обычное сообщение пользователю -> stdout."""
        print(message, flush=True)
        self._raw("INFO", message)

    def error(self, message: str) -> None:
        """Ошибка для пользователя -> stderr."""
        print(f"ОШИБКА: {message}", file=sys.stderr, flush=True)
        self._raw("ERROR", message)

    # ---------------- «сырой» лог в файл ---------------- #

    def raw(self, message: str) -> None:
        """Техническая строка без обработки — только в файл-дубль."""
        self._raw("RAW", message)

    def _raw(self, level: str, message: str) -> None:
        if self.log_file is None:
            return
        line = f"{_ts()} [{level}] {message}"
        if len(line) > RAW_LINE_LIMIT:
            line = line[:RAW_LINE_LIMIT] + "...<обрезано>"
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            # В файл писать не получилось — предупреждаем один раз, но работу
            # приложения не прерываем.
            if not self._file_error_reported:
                self._file_error_reported = True
                print(
                    f"ПРЕДУПРЕЖДЕНИЕ: не удалось писать в файл журнала "
                    f"({type(exc).__name__}). Работа продолжается без него.",
                    file=sys.stderr, flush=True,
                )

    # ---------------- разбор ошибок БД ---------------- #

    def db_error_text(self, exc: BaseException, action: str = "выполнить запрос") -> str:
        """Превращает исключение драйвера/СУБД в дружелюбную фразу.

        Подробности (SQLSTATE, текст драйвера, имя таблицы/колонки, запрос)
        в консоль не попадают — они уходят только в «сырой» лог.
        """
        self.raw(f"{action}: {type(exc).__name__}: {exc}")
        self.raw("traceback:\n" + traceback.format_exc())

        if isinstance(exc, psycopg.OperationalError):
            return (
                f"не удалось {action}: нет связи с базой данных. "
                "Проверьте доступность сервера БД и параметры подключения."
            )
        if isinstance(exc, psycopg.errors.InsufficientPrivilege):
            return (
                f"не удалось {action}: у вашей учётной записи нет прав "
                "на эту операцию. Обратитесь к администратору базы данных."
            )
        if isinstance(exc, psycopg.errors.UniqueViolation):
            return f"не удалось {action}: такие данные уже есть в базе."
        if isinstance(exc, psycopg.errors.ForeignKeyViolation):
            return (
                f"не удалось {action}: указана ссылка на несуществующую запись "
                "в связанной таблице."
            )
        if isinstance(exc, psycopg.errors.CheckViolation):
            return (
                f"не удалось {action}: значение не удовлетворяет ограничениям "
                "поля (например, цена или количество отрицательные)."
            )
        if isinstance(exc, psycopg.errors.UndefinedColumn):
            return f"не удалось {action}: указанного столбца нет в таблице."
        if isinstance(exc, psycopg.errors.UndefinedTable):
            return f"не удалось {action}: указанной таблицы нет в базе."
        if isinstance(exc, psycopg.errors.InvalidTextRepresentation):
            return (
                f"не удалось {action}: значение не подходит по типу данных "
                "(например, текст вместо числа)."
            )
        if isinstance(exc, psycopg.errors.NumericValueOutOfRange):
            return f"не удалось {action}: числовое значение выходит за допустимые границы."
        if isinstance(exc, psycopg.errors.NotNullViolation):
            return f"не удалось {action}: обязательное поле не заполнено."
        if isinstance(exc, psycopg.IntegrityError):
            return f"не удалось {action}: данные нарушают правила целостности базы."
        if isinstance(exc, psycopg.Error):
            return f"не удалось {action}: база данных отклонила операцию."
        return f"не удалось {action}: непредвиденная ошибка."
