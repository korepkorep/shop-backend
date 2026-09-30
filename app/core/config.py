"""Настройки приложения. Все значения берутся из переменных окружения (см. .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # База данных
    database_url: str = "postgresql+psycopg://shop:shop@localhost:5432/shop"

    # JWT
    jwt_secret: str = "dev-only-secret-change-me-in-env-0123456789"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # Бизнес-правила (BR-01, BR-08)
    reservation_ttl_seconds: int = 900  # 15 минут
    cart_max_quantity: int = 10
    idempotency_key_ttl_hours: int = 24

    # Защита входа (US-02, AC 5)
    login_max_failures_per_minute: int = 5

    # Платёжный провайдер
    psp_base_url: str = "http://localhost:8001"
    psp_webhook_secret: str = "psp-secret-change-me"
    psp_timeout_seconds: float = 5.0
    webhook_max_age_seconds: int = 300

    # Брокеры
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    kafka_bootstrap_servers: str = "localhost:9094"
    orders_topic: str = "orders.events"
    orders_topic_partitions: int = 3

    # Воркеры
    outbox_poll_interval_seconds: float = 1.0
    sweep_interval_seconds: int = 300
    notify_retry_delays_seconds: str = "60,300,900"
    notify_fail_marker: str = "fail"  # письма на адреса с этим словом «падают» — для демо ретраев

    log_level: str = "INFO"

    @property
    def retry_delays(self) -> list[int]:
        return [int(x) for x in self.notify_retry_delays_seconds.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
