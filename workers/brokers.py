"""Общий код для работы с брокерами: топология RabbitMQ, клиенты Kafka, цикл консьюмера."""

import json
import logging
import signal
import time
from collections.abc import Callable

import pika
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from app.core.config import settings

log = logging.getLogger(__name__)

# ---------- RabbitMQ ----------

COMMANDS_EXCHANGE = "shop.commands"
Q_NOTIFICATIONS = "notifications"
Q_NOTIFICATIONS_PARKING = "notifications.parking"
Q_RESERVATIONS_DELAY = "reservations.delay"
Q_RESERVATIONS_EXPIRE = "reservations.expire"


def retry_queue_name(delay_seconds: int) -> str:
    # Задержка в имени очереди: при смене настроек появится новая очередь,
    # а не ошибка «аргументы очереди не совпадают»
    return f"notifications.retry.{delay_seconds}s"


def rabbit_connection() -> pika.BlockingConnection:
    params = pika.URLParameters(settings.rabbitmq_url)
    params.heartbeat = 30
    return pika.BlockingConnection(params)


def declare_topology(channel) -> None:
    """Объявление exchange и очередей. Идемпотентно: можно вызывать при каждом старте.

    notifications.send ──► [notifications] ──► воркер notifications
                                 ▲  ошибка: notifications.retry.<N>s (TTL) ──┘ после 3-й — [notifications.parking]
    reservations.expire.delay ──► [reservations.delay] (без консьюмеров, TTL сообщения = 15 мин)
                                 └─ по истечении TTL, через DLX ──► [reservations.expire] ──► воркер reservations
    """
    channel.exchange_declare(COMMANDS_EXCHANGE, exchange_type="direct", durable=True)

    channel.queue_declare(Q_NOTIFICATIONS, durable=True)
    channel.queue_bind(Q_NOTIFICATIONS, COMMANDS_EXCHANGE, routing_key="notifications.send")
    for delay in settings.retry_delays:
        channel.queue_declare(
            retry_queue_name(delay),
            durable=True,
            arguments={
                "x-message-ttl": delay * 1000,
                "x-dead-letter-exchange": COMMANDS_EXCHANGE,
                "x-dead-letter-routing-key": "notifications.send",
            },
        )
    channel.queue_declare(Q_NOTIFICATIONS_PARKING, durable=True)

    channel.queue_declare(
        Q_RESERVATIONS_DELAY,
        durable=True,
        arguments={"x-dead-letter-exchange": COMMANDS_EXCHANGE, "x-dead-letter-routing-key": "reservations.expire"},
    )
    channel.queue_bind(Q_RESERVATIONS_DELAY, COMMANDS_EXCHANGE, routing_key="reservations.expire.delay")
    channel.queue_declare(Q_RESERVATIONS_EXPIRE, durable=True)
    channel.queue_bind(Q_RESERVATIONS_EXPIRE, COMMANDS_EXCHANGE, routing_key="reservations.expire")


def persistent_properties(message_id: str, message_type: str, headers: dict | None = None, expiration_ms=None):
    return pika.BasicProperties(
        delivery_mode=pika.DeliveryMode.Persistent,  # сообщение переживёт перезапуск RabbitMQ
        content_type="application/json",
        message_id=message_id,
        type=message_type,
        headers=headers or {},
        expiration=str(expiration_ms) if expiration_ms else None,
    )


def run_rabbit_consumer(queue: str, handle: Callable, prefetch: int = 10) -> None:
    """Слушает очередь и переподключается при обрыве связи. handle(channel, method, properties, body)."""
    while True:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            declare_topology(channel)
            channel.basic_qos(prefetch_count=prefetch)  # сколько сообщений берём в работу одновременно
            channel.basic_consume(queue, on_message_callback=handle)
            log.info("Слушаю очередь %s", queue)
            channel.start_consuming()
        except pika.exceptions.AMQPError as exc:
            log.warning("Нет связи с RabbitMQ (%s), переподключаюсь через 5 с", exc)
            time.sleep(5)


# ---------- Kafka ----------

DLQ_SUFFIX = ".dlq"


def kafka_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "acks": "all",  # ждём подтверждения от всех реплик
            "enable.idempotence": True,  # ретраи продюсера не создают дублей в партиции
        }
    )


def ensure_topics() -> None:
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    topics = [
        NewTopic(settings.orders_topic, num_partitions=settings.orders_topic_partitions, replication_factor=1),
        NewTopic(settings.orders_topic + DLQ_SUFFIX, num_partitions=1, replication_factor=1),
    ]
    for topic, future in admin.create_topics(topics, operation_timeout=10, request_timeout=10).items():
        try:
            future.result()
            log.info("Создан топик %s", topic)
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise


def run_kafka_consumer(group_id: str, handle: Callable[[dict], None], max_attempts: int = 3) -> None:
    """At-least-once: offset коммитится только ПОСЛЕ успешной обработки.
    Упали посреди обработки — после рестарта сообщение придёт снова, поэтому handle идемпотентен.
    Сообщение, которое не удаётся обработать max_attempts раз, уходит в DLQ-топик."""
    while True:
        try:
            ensure_topics()
            break
        except Exception as exc:
            log.warning("Kafka недоступна (%s), жду 5 с", exc)
            time.sleep(5)

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",  # новая группа читает топик с самого начала
            "enable.auto.commit": False,
        }
    )
    dlq = kafka_producer()
    consumer.subscribe([settings.orders_topic])
    log.info("Consumer group %s читает топик %s", group_id, settings.orders_topic)

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    try:
        while running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.warning("Ошибка Kafka: %s", msg.error())
                continue
            for attempt in range(1, max_attempts + 1):
                try:
                    handle(json.loads(msg.value()))
                    break
                except Exception:
                    log.exception("Не удалось обработать сообщение (попытка %s)", attempt)
                    time.sleep(attempt)
            else:
                # «Ядовитое» сообщение не должно блокировать партицию: откладываем в DLQ
                dlq.produce(settings.orders_topic + DLQ_SUFFIX, key=msg.key(), value=msg.value())
                dlq.flush(10)
                log.error("Сообщение отправлено в DLQ, offset %s", msg.offset())
            consumer.commit(message=msg, asynchronous=False)
    finally:
        consumer.close()
