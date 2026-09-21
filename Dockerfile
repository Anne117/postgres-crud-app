# =============================================================================
#  Образ приложения (интерпретируемое приложение + все зависимости + конфиг).
#  Сборка:  docker build -t crud-app:0.3.0 .
# =============================================================================
FROM python:3.13-slim

LABEL org.opencontainers.image.title="crud-app" \
      org.opencontainers.image.description="ПЗ №3: CRUD-приложение над PostgreSQL (склад интернет-магазина)" \
      org.opencontainers.image.version="0.3.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_CONFIG=/opt/app/config/app.docker.conf

WORKDIR /opt/app

# Зависимости приложения.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код, конфиг-файл и вспомогательные SQL-скрипты.
COPY src/   ./src/
COPY config/ ./config/
COPY sql/    ./sql/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /opt/app/logs

# Непривилегированный пользователь внутри контейнера.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /opt/app
USER appuser

# Интерактивный режим: приложение читает данные из stdin.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "src/app.py"]
