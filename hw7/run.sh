#!/usr/bin/env bash
# Запуск задания №7 одной командой.
#   ./run.sh          — собрать и поднять
#   ./run.sh down     — остановить
#   ./run.sh logs     — логи приложения
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-up}" in
  up)
    docker compose -f docker-compose.web.yml up -d --build
    echo
    echo "Приложение: http://localhost:8081"
    echo "Вход: app_user / user_secret  (или app_admin / adminsecret)"
    ;;
  down) docker compose -f docker-compose.web.yml down ;;
  logs) docker compose -f docker-compose.web.yml logs -f web ;;
  *) echo "usage: $0 [up|down|logs]"; exit 1 ;;
esac
