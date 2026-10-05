-- Учебные данные для тренировки SQL. Заливаются в ОТДЕЛЬНУЮ базу shop_practice,
-- рабочая база shop не затрагивается. Как залить — см. practice/README.md.
-- Данные генерируются случайно, но одинаково при каждом запуске (setseed).

SELECT setseed(0.42);

-- ---------- Пользователи: 1 админ и 40 покупателей (у последних 10 заказов не будет) ----------
INSERT INTO users (id, email, password_hash, role, created_at)
VALUES (1, 'admin@example.com', 'practice', 'admin', now() - interval '120 days');

INSERT INTO users (id, email, password_hash, role, created_at)
SELECT g + 1, 'user' || g || '@example.com', 'practice', 'customer',
       now() - interval '1 day' * (60 + floor(random() * 60))
FROM generate_series(1, 40) AS g;

-- ---------- Каталог ----------
INSERT INTO categories (id, name, parent_id) VALUES
  (1, 'Смартфоны и гаджеты', NULL),
  (2, 'Смартфоны', 1),
  (3, 'Наушники', 1),
  (4, 'Умные часы', 1),
  (5, 'Компьютеры', NULL),
  (6, 'Ноутбуки', 5),
  (7, 'Мониторы', 5),
  (8, 'Аксессуары', 5);

INSERT INTO products (id, category_id, sku, name, description, price_kopecks, is_active, created_at, updated_at) VALUES
  (1, 2, 'PH-VOLT-X1', 'Смартфон Volt X1 128 ГБ', '', 3499000, true, now() - interval '100 days', now()),
  (2, 2, 'PH-VOLT-X1P', 'Смартфон Volt X1 Pro 256 ГБ', '', 5499000, true, now() - interval '100 days', now()),
  (3, 2, 'PH-NOVA-5', 'Смартфон Nova 5 64 ГБ', '', 1599000, true, now() - interval '95 days', now()),
  (4, 2, 'PH-LAST-1', 'Смартфон Limited Edition', '', 9999000, true, now() - interval '10 days', now()),
  (5, 3, 'HP-BEAT-AIR', 'Наушники Beat Air', '', 799000, true, now() - interval '100 days', now()),
  (6, 3, 'HP-BEAT-PRO', 'Наушники Beat Pro', '', 1999000, true, now() - interval '90 days', now()),
  (7, 4, 'WT-PULSE-2', 'Умные часы Pulse 2', '', 1299000, true, now() - interval '80 days', now()),
  (8, 6, 'NB-AIRBOOK-13', 'Ноутбук AirBook 13', '', 8999000, true, now() - interval '100 days', now()),
  (9, 6, 'NB-WORK-15', 'Ноутбук WorkStation 15', '', 12999000, true, now() - interval '70 days', now()),
  (10, 7, 'MN-VIEW-27', 'Монитор View 27 4K', '', 3299000, true, now() - interval '100 days', now()),
  (11, 8, 'AC-MOUSE-1', 'Мышь беспроводная M1', '', 149000, true, now() - interval '100 days', now()),
  (12, 8, 'AC-KEYB-1', 'Клавиатура механическая K1', '', 599000, true, now() - interval '60 days', now()),
  (13, 8, 'AC-HUB-7', 'USB-C хаб 7 в 1', '', 349000, true, now() - interval '5 days', now());

INSERT INTO stock (product_id, quantity, reserved, updated_at) VALUES
  (1, 25, 0, now()), (2, 10, 0, now()), (3, 40, 0, now()), (4, 1, 0, now()), (5, 60, 0, now()),
  (6, 15, 0, now()), (7, 30, 0, now()), (8, 8, 0, now()), (9, 5, 0, now()), (10, 12, 0, now()),
  (11, 200, 0, now()), (12, 50, 0, now()), (13, 0, 0, now());

-- ---------- Заказы: 300 штук за последние 60 дней ----------
-- Заказы есть только у user1..user30. Товары 4 и 13 никто не заказывал.
INSERT INTO orders (id, user_id, status, total_kopecks, expires_at, created_at, updated_at)
SELECT g,
       2 + floor(random() * 30)::int,
       CASE
         WHEN r < 0.38 THEN 'delivered'
         WHEN r < 0.50 THEN 'shipped'
         WHEN r < 0.66 THEN 'paid'
         WHEN r < 0.82 THEN 'expired'
         WHEN r < 0.92 THEN 'cancelled'
         ELSE 'refunded'
       END,
       1,  -- временно, пересчитаем после позиций
       ts + interval '15 minutes',
       ts,
       ts
FROM (
  SELECT g, random() AS r,
         now() - interval '60 days' + interval '1 second' * floor(random() * 60 * 24 * 3600) AS ts
  FROM generate_series(1, 300) AS g
) AS s;

-- Три свежих неоплаченных заказа (статус created, товар в резерве)
INSERT INTO orders (id, user_id, status, total_kopecks, expires_at, created_at, updated_at) VALUES
  (301, 5, 'created', 1, now() + interval '10 minutes', now() - interval '5 minutes', now()),
  (302, 12, 'created', 1, now() + interval '13 minutes', now() - interval '2 minutes', now()),
  (303, 5, 'created', 1, now() + interval '14 minutes', now() - interval '1 minute', now());

-- Позиции: 1–3 разных товара в заказе
INSERT INTO order_items (order_id, product_id, product_name, quantity, price_kopecks)
SELECT o.id, p.id, p.name, 1 + floor(random() * 2)::int, p.price_kopecks
FROM orders AS o
CROSS JOIN LATERAL (
  SELECT id, name, price_kopecks,
         row_number() OVER (ORDER BY random() + o.id * 0) AS rn
  FROM products
  WHERE id NOT IN (4, 13)
) AS p
WHERE p.rn <= 1 + (o.id % 3);

UPDATE orders AS o
SET total_kopecks = t.total
FROM (SELECT order_id, SUM(quantity * price_kopecks) AS total FROM order_items GROUP BY order_id) AS t
WHERE t.order_id = o.id;

-- Резерв под неоплаченные заказы
UPDATE stock AS s
SET reserved = r.qty
FROM (
  SELECT oi.product_id, SUM(oi.quantity) AS qty
  FROM order_items AS oi JOIN orders AS o ON o.id = oi.order_id
  WHERE o.status = 'created'
  GROUP BY oi.product_id
) AS r
WHERE r.product_id = s.product_id;

-- ---------- История статусов ----------
-- Время оплаты: через 1–14 минут после оформления
CREATE TEMP TABLE paid_at AS
SELECT id AS order_id, created_at + interval '1 minute' * (1 + floor(random() * 14)) AS ts
FROM orders WHERE status IN ('paid', 'shipped', 'delivered', 'refunded');

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT id, NULL, 'created', 'customer', 'created', created_at FROM orders;

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT order_id, 'created', 'paid', 'psp', 'payment_succeeded', ts FROM paid_at;

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT o.id, 'paid', 'shipped', 'admin', 'marked_shipped', p.ts + interval '1 day' * (1 + floor(random() * 2))
FROM orders AS o JOIN paid_at AS p ON p.order_id = o.id
WHERE o.status IN ('shipped', 'delivered');

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT h.order_id, 'shipped', 'delivered', 'admin', 'marked_delivered', h.created_at + interval '1 day' * (2 + floor(random() * 4))
FROM order_status_history AS h JOIN orders AS o ON o.id = h.order_id
WHERE h.to_status = 'shipped' AND o.status = 'delivered';

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT o.id, 'paid', 'refunded', 'psp', 'refund_completed', p.ts + interval '1 day' * (1 + floor(random() * 5))
FROM orders AS o JOIN paid_at AS p ON p.order_id = o.id
WHERE o.status = 'refunded';

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT id, 'created', 'expired', 'system', 'payment_timeout:rabbitmq_ttl', expires_at
FROM orders WHERE status = 'expired';

INSERT INTO order_status_history (order_id, from_status, to_status, actor, reason, created_at)
SELECT id, 'created', 'cancelled', 'customer', 'cancelled_by_customer', created_at + interval '1 minute' * (1 + floor(random() * 10))
FROM orders WHERE status = 'cancelled';

UPDATE orders AS o
SET updated_at = h.last_change
FROM (SELECT order_id, MAX(created_at) AS last_change FROM order_status_history GROUP BY order_id) AS h
WHERE h.order_id = o.id;

-- ---------- Платежи и возвраты ----------
-- Успешные платежи
INSERT INTO payments (order_id, provider_payment_id, amount_kopecks, status, payment_url, created_at, updated_at)
SELECT o.id, 'pay_' || substr(md5('ok' || o.id), 1, 16), o.total_kopecks,
       CASE WHEN o.status = 'refunded' THEN 'refunded' ELSE 'succeeded' END,
       'http://localhost:8001/pay/' || o.id, p.ts - interval '1 minute', p.ts
FROM orders AS o JOIN paid_at AS p ON p.order_id = o.id;

-- Неудачные попытки: у части истёкших заказов и у каждого десятого оплаченного (до успешной)
INSERT INTO payments (order_id, provider_payment_id, amount_kopecks, status, payment_url, created_at, updated_at)
SELECT o.id, 'pay_' || substr(md5('fail' || o.id), 1, 16), o.total_kopecks, 'failed',
       'http://localhost:8001/pay/' || o.id, o.created_at + interval '1 minute', o.created_at + interval '2 minutes'
FROM orders AS o
WHERE (o.status = 'expired' AND o.id % 3 = 0)
   OR (o.status IN ('paid', 'shipped', 'delivered') AND o.id % 10 = 0);

INSERT INTO refunds (payment_id, provider_refund_id, amount_kopecks, status, reason, created_at, updated_at)
SELECT pm.id, 'ref_' || substr(md5('ref' || pm.id), 1, 16), pm.amount_kopecks, 'succeeded', 'admin', h.created_at, h.created_at
FROM payments AS pm
JOIN order_status_history AS h ON h.order_id = pm.order_id AND h.to_status = 'refunded'
WHERE pm.status = 'refunded';

-- ---------- После продаж ----------
-- Две цены выросли: в старых заказах осталась прежняя цена (BR-02)
UPDATE products SET price_kopecks = 3799000, updated_at = now() WHERE id = 1;
UPDATE products SET price_kopecks = 899000, updated_at = now() WHERE id = 5;
-- Один товар снят с продажи
UPDATE products SET is_active = false, updated_at = now() WHERE id = 6;

-- Счётчики id продолжаются после вставленных данных
SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));
SELECT setval('categories_id_seq', (SELECT MAX(id) FROM categories));
SELECT setval('products_id_seq', (SELECT MAX(id) FROM products));
SELECT setval('orders_id_seq', (SELECT MAX(id) FROM orders));
