#!/bin/sh
# =============================================================================
#  Точка входа образа приложения.
#    1) ждёт доступности сервиса СУБД в сети Docker (сокет host:port из
#       конфиг-файла приложения, по умолчанию db:5432);
#    2) запускает приложение в интерактивном режиме.
#  Ошибки подключения сообщаются пользователю дружелюбно, без деталей
#  реализации.
# =============================================================================
set -eu

HOST="${DB_HOST:-db}"
PORT="${DB_PORT:-5432}"
TIMEOUT="${DB_WAIT_TIMEOUT:-60}"

echo "Ожидание сервера базы данных ${HOST}:${PORT} (до ${TIMEOUT} с)..."
i=0
while [ "$i" -lt "$TIMEOUT" ]; do
    if python - "$HOST" "$PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(2)
try:
    s.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
    then
        echo "Сервер базы данных доступен."
        break
    fi
    i=$((i + 1))
    sleep 1
done

if [ "$i" -ge "$TIMEOUT" ]; then
    echo "ОШИБКА: сервер базы данных не отвечает. Проверьте, что контейнер СУБД запущен и находится в одной сети Docker с приложением." >&2
    exit 1
fi

exec "$@"
