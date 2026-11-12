"""The website's read-only views, with sale prices, shared page photos and product specifications (specs).

price         = online price (phone, else product, else shop price) minus the product's percentage off
compare_price = the crossed-out "was" price: the product's compare price, else the price before the percentage off, else NULL
Photos are shared by every variant on a page, or belong to one colour/storage only (variant_product_id)."""
from django.db import migrations

ENABLED="(SELECT id FROM shop_branch WHERE enabled AND NOT is_online)"
RESERVED="COALESCE((SELECT SUM(a.quantity) FROM shop_onlineallocation a JOIN shop_onlineorderline l ON l.id = a.line_id JOIN shop_onlineorder o ON o.id = l.order_id WHERE a.product_id = {p} AND a.device_id IS NULL AND o.status = 'New'{b}), 0)"
BASE_PHONE="COALESCE(d.online_price, p.online_price, d.asking_price)"
DISC="COALESCE(p.discount_percent, 0)"
PRICE_PHONE=f"ROUND({BASE_PHONE} * (100 - {DISC}) / 100, 2)"
COMPARE_PHONE=f"CASE WHEN p.compare_price IS NOT NULL THEN p.compare_price WHEN {DISC} > 0 THEN {BASE_PHONE} ELSE NULL END"
BASE_ACC="COALESCE(p.online_price, p.selling_price)"
PRICE_ACC=f"ROUND({BASE_ACC} * (100 - {DISC}) / 100, 2)"
COMPARE_ACC=f"CASE WHEN p.compare_price IS NOT NULL THEN p.compare_price WHEN {DISC} > 0 THEN {BASE_ACC} ELSE NULL END"
ACC_STOCK=f"(SELECT COALESCE(SUM(sm.quantity), 0) FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id WHERE sm.product_id = p.id AND w.branch_id IN {ENABLED}) - {RESERVED.format(p='p.id',b='')}"
FAMILY="COALESCE(NULLIF(q.family_slug, ''), q.slug)"

LISTINGS=f"""
CREATE VIEW web_listings AS
SELECT p.id AS product_id, p.slug AS slug, COALESCE(NULLIF(p.family_slug, ''), p.slug) AS family_slug,
       COALESCE(NULLIF(p.web_title, ''), COALESCE(m.brand, p.brand) || ' ' || m.name, p.name) AS page_title,
       p.name AS name, COALESCE(m.brand, p.brand) AS brand, COALESCE(m.name, p.name) AS model,
       p.storage AS storage, p.colour AS colour, p."group" AS category, p.description AS description, p.specs AS specs, p.serialized AS is_phone,
       COALESCE(p.discount_percent, 0) AS discount_percent,
       CASE WHEN p.serialized THEN (SELECT MIN({PRICE_PHONE}) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN {ENABLED}) ELSE {PRICE_ACC} END AS price_from,
       CASE WHEN p.serialized THEN (SELECT MAX({PRICE_PHONE}) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN {ENABLED}) ELSE {PRICE_ACC} END AS price_to,
       CASE WHEN p.serialized THEN (SELECT COUNT(*) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN {ENABLED}) ELSE {ACC_STOCK} END AS stock_total,
       COALESCE((SELECT i.url FROM shop_productimage i WHERE i.product_id = p.id AND i.variant_only ORDER BY i.position, i.created LIMIT 1),
                (SELECT i.url FROM shop_productimage i JOIN shop_product q ON q.id = i.product_id WHERE NOT i.variant_only AND {FAMILY} = COALESCE(NULLIF(p.family_slug, ''), p.slug) ORDER BY i.position, i.created LIMIT 1)) AS cover_url
FROM shop_product p LEFT JOIN shop_phonemodel m ON m.id = p.model_id
WHERE p.online_visible AND p.enabled AND p.slug IS NOT NULL
"""

FAMILIES="""
CREATE VIEW web_families AS
SELECT g.family_slug AS family_slug, g.title AS title, g.brand AS brand, g.category AS category, g.description AS description,
       (SELECT l.specs FROM web_listings l WHERE l.family_slug = g.family_slug ORDER BY l.slug LIMIT 1) AS specs,
       g.is_phone AS is_phone, g.price_from AS price_from, g.price_to AS price_to, g.discount_percent AS discount_percent, g.stock_total AS stock_total, g.variant_count AS variant_count, g.cover_url AS cover_url
FROM (SELECT family_slug AS family_slug, MIN(page_title) AS title, MIN(brand) AS brand, MIN(category) AS category, MIN(description) AS description, MAX(CASE WHEN is_phone THEN 1 ELSE 0 END) AS is_phone,
             MIN(price_from) AS price_from, MAX(price_to) AS price_to, MAX(discount_percent) AS discount_percent, SUM(stock_total) AS stock_total, COUNT(*) AS variant_count, MIN(cover_url) AS cover_url
      FROM web_listings GROUP BY family_slug) g
"""

VARIANTS=f"""
CREATE VIEW web_variants AS
SELECT x.product_id AS product_id, x.condition AS condition, x.pta_status AS pta_status, x.device_type AS device_type, x.price AS price, MIN(x.compare_price) AS compare_price, COUNT(*) AS quantity,
       MIN(x.grade) AS grade, MIN(x.battery_health) AS battery_health, MIN(x.warranty_days) AS warranty_days
FROM (SELECT d.product_id AS product_id, d.condition AS condition, d.pta_status AS pta_status, d.device_type AS device_type, d.grade AS grade,
             CASE WHEN d.battery_checked THEN d.battery ELSE NULL END AS battery_health, d.warranty_days AS warranty_days,
             {PRICE_PHONE} AS price, {COMPARE_PHONE} AS compare_price
      FROM shop_device d JOIN shop_product p ON p.id = d.product_id
      WHERE d.status = 'Available' AND d.branch_id IN {ENABLED} AND p.online_visible AND p.enabled) x
GROUP BY x.product_id, x.condition, x.pta_status, x.device_type, x.price
UNION ALL
SELECT p.id, '', '', '', {PRICE_ACC}, {COMPARE_ACC}, {ACC_STOCK}, NULL, NULL, NULL
FROM shop_product p
WHERE NOT p.serialized AND p.stock_item AND p.online_visible AND p.enabled AND {ACC_STOCK} > 0
"""

VARIANT_STOCK=f"""
CREATE VIEW web_variant_stock AS
SELECT d.product_id AS product_id, d.condition AS condition, d.pta_status AS pta_status, d.device_type AS device_type,
       {PRICE_PHONE} AS price, b.id AS shop_id, b.name AS shop_name, COUNT(*) AS quantity
FROM shop_device d JOIN shop_product p ON p.id = d.product_id JOIN shop_branch b ON b.id = d.branch_id
WHERE d.status = 'Available' AND b.enabled AND NOT b.is_online AND p.online_visible AND p.enabled
GROUP BY d.product_id, d.condition, d.pta_status, d.device_type, {PRICE_PHONE}, b.id, b.name
UNION ALL
SELECT p.id, '', '', '', {PRICE_ACC}, b.id, b.name,
       SUM(sm.quantity) - {RESERVED.format(p='p.id',b=' AND a.branch_id = b.id')}
FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id JOIN shop_branch b ON b.id = w.branch_id JOIN shop_product p ON p.id = sm.product_id
WHERE NOT p.serialized AND b.enabled AND NOT b.is_online AND p.online_visible AND p.enabled
GROUP BY p.id, p.online_price, p.selling_price, p.discount_percent, b.id, b.name
HAVING SUM(sm.quantity) - {RESERVED.format(p='p.id',b=' AND a.branch_id = b.id')} > 0
"""

STOCK="""
CREATE VIEW web_stock AS
SELECT product_id AS product_id, shop_id AS shop_id, shop_name AS shop_name, SUM(quantity) AS quantity FROM web_variant_stock GROUP BY product_id, shop_id, shop_name
"""

UNITS=f"""
CREATE VIEW web_phone_units AS
SELECT d.id AS unit_id, d.product_id AS product_id, b.id AS shop_id, b.name AS shop_name, d.condition AS condition, d.grade AS grade,
       CASE WHEN d.battery_checked THEN d.battery ELSE NULL END AS battery_health, d.pta_status AS pta_status, d.device_type AS device_type,
       d.warranty_days AS warranty_days, {PRICE_PHONE} AS price, {COMPARE_PHONE} AS compare_price
FROM shop_device d JOIN shop_branch b ON b.id = d.branch_id JOIN shop_product p ON p.id = d.product_id
WHERE d.status = 'Available' AND b.enabled AND NOT b.is_online AND p.online_visible AND p.enabled
"""

IMAGES="""
CREATE VIEW web_images AS
SELECT i.id AS image_id, COALESCE(NULLIF(p.family_slug, ''), p.slug) AS family_slug, CASE WHEN i.variant_only THEN i.product_id ELSE NULL END AS variant_product_id,
       i.product_id AS product_id, i.url AS url, i.alt AS alt, i.position AS position
FROM shop_productimage i JOIN shop_product p ON p.id = i.product_id
WHERE p.online_visible AND p.enabled
"""

DROP_ORDER=('web_families','web_stock','web_variant_stock','web_variants','web_phone_units','web_images','web_listings')


def create(apps,schema_editor):
    for name in DROP_ORDER:schema_editor.execute('DROP VIEW IF EXISTS '+name)
    for sql in (LISTINGS,FAMILIES,VARIANTS,VARIANT_STOCK,STOCK,UNITS,IMAGES):schema_editor.execute(sql)


def drop(apps,schema_editor):
    for name in DROP_ORDER:schema_editor.execute('DROP VIEW IF EXISTS '+name)


class Migration(migrations.Migration):
    dependencies=[('shop','0027_specs_order_details')]
    operations=[migrations.RunPython(create,drop)]
