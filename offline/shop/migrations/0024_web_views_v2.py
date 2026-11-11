"""The website's read-only views, final form. Pages (families) with variants and per-shop stock, counting only what can really be sold:
phones that are Available (not Reserved by an order, a transfer or a sale) and accessory units not held by a new online order.
Never exposes cost, IMEI, suppliers or customers. Recreated here because migration 0023 had to drop them to change tables on SQLite."""
from django.db import migrations

ENABLED="(SELECT id FROM shop_branch WHERE enabled)"
RESERVED="COALESCE((SELECT SUM(a.quantity) FROM shop_onlineallocation a JOIN shop_onlineorderline l ON l.id = a.line_id JOIN shop_onlineorder o ON o.id = l.order_id WHERE a.product_id = {p} AND a.device_id IS NULL AND o.status = 'New'{b}), 0)"

LISTINGS=f"""
CREATE VIEW web_listings AS
SELECT p.id AS product_id, p.slug AS slug,
       COALESCE(NULLIF(p.family_slug, ''), p.slug) AS family_slug,
       COALESCE(NULLIF(p.web_title, ''), COALESCE(m.brand, p.brand) || ' ' || m.name, p.name) AS page_title,
       p.name AS name, COALESCE(m.brand, p.brand) AS brand, COALESCE(m.name, p.name) AS model,
       p.storage AS storage, p.colour AS colour, p."group" AS category, p.description AS description, p.serialized AS is_phone,
       CASE WHEN p.serialized THEN (SELECT MIN(COALESCE(d.online_price, p.online_price, d.asking_price)) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN {ENABLED})
            ELSE COALESCE(p.online_price, p.selling_price) END AS price_from,
       CASE WHEN p.serialized THEN (SELECT MAX(COALESCE(d.online_price, p.online_price, d.asking_price)) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN {ENABLED})
            ELSE COALESCE(p.online_price, p.selling_price) END AS price_to,
       CASE WHEN p.serialized THEN (SELECT COUNT(*) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN {ENABLED})
            ELSE (SELECT COALESCE(SUM(sm.quantity), 0) FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id WHERE sm.product_id = p.id AND w.branch_id IN {ENABLED}) - {RESERVED.format(p='p.id',b='')} END AS stock_total,
       (SELECT i.url FROM shop_productimage i WHERE i.product_id = p.id ORDER BY i.position, i.created LIMIT 1) AS cover_url
FROM shop_product p LEFT JOIN shop_phonemodel m ON m.id = p.model_id
WHERE p.online_visible AND p.enabled AND p.slug IS NOT NULL
"""

FAMILIES="""
CREATE VIEW web_families AS
SELECT family_slug AS family_slug, MIN(page_title) AS title, MIN(brand) AS brand, MIN(category) AS category, MIN(description) AS description, MAX(CASE WHEN is_phone THEN 1 ELSE 0 END) AS is_phone,
       MIN(price_from) AS price_from, MAX(price_to) AS price_to, SUM(stock_total) AS stock_total, COUNT(*) AS variant_count, MIN(cover_url) AS cover_url
FROM web_listings GROUP BY family_slug
"""

VARIANTS=f"""
CREATE VIEW web_variants AS
SELECT x.product_id AS product_id, x.condition AS condition, x.pta_status AS pta_status, x.device_type AS device_type, x.price AS price, COUNT(*) AS quantity,
       MIN(x.grade) AS grade, MIN(x.battery_health) AS battery_health, MIN(x.warranty_days) AS warranty_days
FROM (SELECT d.product_id AS product_id, d.condition AS condition, d.pta_status AS pta_status, d.device_type AS device_type, d.grade AS grade,
             CASE WHEN d.battery_checked THEN d.battery ELSE NULL END AS battery_health, d.warranty_days AS warranty_days,
             COALESCE(d.online_price, p.online_price, d.asking_price) AS price
      FROM shop_device d JOIN shop_product p ON p.id = d.product_id
      WHERE d.status = 'Available' AND d.branch_id IN {ENABLED} AND p.online_visible AND p.enabled) x
GROUP BY x.product_id, x.condition, x.pta_status, x.device_type, x.price
UNION ALL
SELECT p.id, '', '', '', COALESCE(p.online_price, p.selling_price),
       (SELECT COALESCE(SUM(sm.quantity), 0) FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id WHERE sm.product_id = p.id AND w.branch_id IN {ENABLED}) - {RESERVED.format(p='p.id',b='')},
       NULL, NULL, NULL
FROM shop_product p
WHERE NOT p.serialized AND p.stock_item AND p.online_visible AND p.enabled
  AND (SELECT COALESCE(SUM(sm.quantity), 0) FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id WHERE sm.product_id = p.id AND w.branch_id IN {ENABLED}) - {RESERVED.format(p='p.id',b='')} > 0
"""

VARIANT_STOCK=f"""
CREATE VIEW web_variant_stock AS
SELECT d.product_id AS product_id, d.condition AS condition, d.pta_status AS pta_status, d.device_type AS device_type,
       COALESCE(d.online_price, p.online_price, d.asking_price) AS price, b.id AS shop_id, b.name AS shop_name, COUNT(*) AS quantity
FROM shop_device d JOIN shop_product p ON p.id = d.product_id JOIN shop_branch b ON b.id = d.branch_id
WHERE d.status = 'Available' AND b.enabled AND p.online_visible AND p.enabled
GROUP BY d.product_id, d.condition, d.pta_status, d.device_type, COALESCE(d.online_price, p.online_price, d.asking_price), b.id, b.name
UNION ALL
SELECT p.id, '', '', '', COALESCE(p.online_price, p.selling_price), b.id, b.name,
       SUM(sm.quantity) - {RESERVED.format(p='p.id',b=' AND a.branch_id = b.id')}
FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id JOIN shop_branch b ON b.id = w.branch_id JOIN shop_product p ON p.id = sm.product_id
WHERE NOT p.serialized AND b.enabled AND p.online_visible AND p.enabled
GROUP BY p.id, p.online_price, p.selling_price, b.id, b.name
HAVING SUM(sm.quantity) - {RESERVED.format(p='p.id',b=' AND a.branch_id = b.id')} > 0
"""

STOCK="""
CREATE VIEW web_stock AS
SELECT product_id AS product_id, shop_id AS shop_id, shop_name AS shop_name, SUM(quantity) AS quantity FROM web_variant_stock GROUP BY product_id, shop_id, shop_name
"""

UNITS="""
CREATE VIEW web_phone_units AS
SELECT d.id AS unit_id, d.product_id AS product_id, b.id AS shop_id, b.name AS shop_name, d.condition AS condition, d.grade AS grade,
       CASE WHEN d.battery_checked THEN d.battery ELSE NULL END AS battery_health, d.pta_status AS pta_status, d.device_type AS device_type,
       d.warranty_days AS warranty_days, COALESCE(d.online_price, p.online_price, d.asking_price) AS price
FROM shop_device d JOIN shop_branch b ON b.id = d.branch_id JOIN shop_product p ON p.id = d.product_id
WHERE d.status = 'Available' AND b.enabled AND p.online_visible AND p.enabled
"""

IMAGES="""
CREATE VIEW web_images AS
SELECT i.id AS image_id, i.product_id AS product_id, i.url AS url, i.alt AS alt, i.position AS position
FROM shop_productimage i JOIN shop_product p ON p.id = i.product_id
WHERE p.online_visible AND p.enabled
"""

ORDER=('web_listings','web_families','web_variants','web_variant_stock','web_stock','web_phone_units','web_images')
DROP_ORDER=('web_families','web_stock','web_variant_stock','web_variants','web_phone_units','web_images','web_listings')


def create(apps,schema_editor):
    for name in DROP_ORDER:schema_editor.execute('DROP VIEW IF EXISTS '+name)
    for sql in (LISTINGS,FAMILIES,VARIANTS,VARIANT_STOCK,STOCK,UNITS,IMAGES):schema_editor.execute(sql)


def drop(apps,schema_editor):
    for name in DROP_ORDER:schema_editor.execute('DROP VIEW IF EXISTS '+name)


class Migration(migrations.Migration):
    dependencies=[('shop','0023_online_orders')]
    operations=[migrations.RunPython(create,drop)]
