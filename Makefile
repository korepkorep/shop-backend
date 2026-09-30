# Короткие команды: make up, make test и т.д. На маке make уже установлен.

up:        ## Поднять всю систему
	docker compose up --build -d

down:      ## Остановить
	docker compose down

reset:     ## Остановить и удалить все данные (БД, очереди, топики)
	docker compose down -v

logs:      ## Логи всех сервисов
	docker compose logs -f --tail=50

api:       ## Только БД + API + мок платёжки
	docker compose up --build -d api psp-mock

test:      ## Автотесты в Docker
	docker compose --profile test run --rm --build tests

dashboard: ## Дашборд продаж
	docker compose --profile dashboard up --build -d dashboard

lint:
	ruff check . && ruff format --check .

.PHONY: up down reset logs api test dashboard lint
