"""Read-only views for the website. They expose only public facts (name, photos, price, quantity per shop): never cost, IMEI,
suppliers, customers or accounts. Give the website a read-only database user that can SELECT from these views only."""
from django.db import migrations

LISTINGS="""
CREATE VIEW web_listings AS
SELECT p.id AS product_id, p.slug AS slug, p.name AS name, COALESCE(m.brand, p.brand) AS brand, COALESCE(m.name, p.name) AS model,
       p.storage AS storage, p.colour AS colour, p."group" AS category, p.description AS description, p.serialized AS is_phone,
       CASE WHEN p.serialized THEN (SELECT MIN(COALESCE(d.online_price, p.online_price, d.asking_price)) FROM shop_device d
                                     WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN (SELECT id FROM shop_branch WHERE enabled))
            ELSE COALESCE(p.online_price, p.selling_price) END AS price_from,
       CASE WHEN p.serialized THEN (SELECT MAX(COALESCE(d.online_price, p.online_price, d.asking_price)) FROM shop_device d
                                     WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN (SELECT id FROM shop_branch WHERE enabled))
            ELSE COALESCE(p.online_price, p.selling_price) END AS price_to,
       CASE WHEN p.serialized THEN (SELECT COUNT(*) FROM shop_device d WHERE d.product_id = p.id AND d.status = 'Available' AND d.branch_id IN (SELECT id FROM shop_branch WHERE enabled))
            ELSE (SELECT COALESCE(SUM(sm.quantity), 0) FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id
                  WHERE sm.product_id = p.id AND w.branch_id IN (SELECT id FROM shop_branch WHERE enabled)) END AS stock_total,
       (SELECT i.url FROM shop_productimage i WHERE i.product_id = p.id ORDER BY i.position, i.created LIMIT 1) AS cover_url
FROM shop_product p LEFT JOIN shop_phonemodel m ON m.id = p.model_id
WHERE p.online_visible AND p.enabled AND p.slug IS NOT NULL
"""

STOCK="""
CREATE VIEW web_stock AS
SELECT d.product_id AS product_id, b.id AS shop_id, b.name AS shop_name, COUNT(*) AS quantity
FROM shop_device d JOIN shop_branch b ON b.id = d.branch_id JOIN shop_product p ON p.id = d.product_id
WHERE d.status = 'Available' AND b.enabled AND p.online_visible AND p.enabled
GROUP BY d.product_id, b.id, b.name
UNION ALL
SELECT sm.product_id AS product_id, b.id AS shop_id, b.name AS shop_name, SUM(sm.quantity) AS quantity
FROM shop_stockmovement sm JOIN shop_warehouse w ON w.id = sm.warehouse_id JOIN shop_branch b ON b.id = w.branch_id JOIN shop_product p ON p.id = sm.product_id
WHERE NOT p.serialized AND b.enabled AND p.online_visible AND p.enabled
GROUP BY sm.product_id, b.id, b.name
HAVING SUM(sm.quantity) > 0
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

NAMES=('web_listings','web_stock','web_phone_units','web_images')


def create(apps,schema_editor):
    for sql in (LISTINGS,STOCK,UNITS,IMAGES):schema_editor.execute(sql)


def drop(apps,schema_editor):
    for name in reversed(NAMES):schema_editor.execute('DROP VIEW IF EXISTS '+name)


class Migration(migrations.Migration):
    dependencies=[('shop','0021_online_store')]
    operations=[migrations.RunPython(create,drop)]
