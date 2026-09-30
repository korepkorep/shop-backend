#!/bin/sh
# Старт API: применить миграции, залить тестовые данные (если БД пустая), запустить сервер
set -e
alembic upgrade head
python -m scripts.seed
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
