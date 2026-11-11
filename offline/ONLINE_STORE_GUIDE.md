# Online store: what the website reads

The ERP is the dashboard for the website. The owner adds photos, a description and an optional online price under **Online Store → Products & Photos**, and publishes a product. A product appears on the website only when it is **Published**, has **at least one photo** and **a price**. Shop prices, stock and the books are never changed from there.

## Prices

| Price | Where it is set | Used by |
| --- | --- | --- |
| Shop price | When stock is bought (every phone and accessory needs one) | POS and the shops |
| Online price (product) | Online Store → product | The website (all units of that product) |
| Online price (one phone) | The phone's own record | The website (that phone only) |

The website price is the phone's online price, else the product's online price, else the shop price.

## Photos

Public Supabase Storage bucket `product-images` (created automatically on the first upload). JPG, PNG or WebP, 5 MB each, 12 per product. The browser resizes large photos to 1600 px before uploading. The first photo is the cover; drag to reorder.

## Read-only views for the website (migration 0022)

| View | One row per | Columns |
| --- | --- | --- |
| `web_listings` | published product | product_id, slug, name, brand, model, storage, colour, category, description, is_phone, price_from, price_to, stock_total, cover_url |
| `web_stock` | product and shop | product_id, shop_id, shop_name, quantity |
| `web_phone_units` | available phone | unit_id, product_id, shop_id, shop_name, condition, grade, battery_health, pta_status, device_type, warranty_days, price |
| `web_images` | photo | image_id, product_id, url, alt, position |

The views contain no cost, IMEI, supplier, customer or account data. Stock follows the ERP at once: a phone sold, reserved or in transit disappears from `web_stock`, and a product with no stock can be hidden by the website.

Give the website a **read-only** login that can only read these four views. Run once in the Supabase SQL editor (choose your own password):

```sql
create role web_reader login password 'CHANGE-ME';
grant usage on schema public to web_reader;
grant select on web_listings, web_stock, web_phone_units, web_images to web_reader;
```

Never give the website the ERP's own database login. If a later ERP migration changes a column these views use, drop and recreate the views in that migration.
