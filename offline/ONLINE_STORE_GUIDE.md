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

---

# Online orders, stock, accounting (how the website and the ERP fit together)

## One stock, one set of books
There is no separate online stock. A phone or accessory in any shop can be sold online. The moment a website order arrives its stock is **reserved** (phones become *Reserved*, so the shop's POS cannot sell them; accessory units are held back). When the order is **dispatched**, one ordinary sales invoice is posted in each shop that supplied stock, tagged **Online**. So:
* a phone sold online is **Sold in the shop** that held it, and its cost, revenue and receivable are in that shop's books;
* a phone sold in a shop disappears from the website at once;
* the same phone held in several shops is **one variant with the total quantity**; the order is supplied from one shop when one shop holds everything, otherwise split (one invoice per shop). The order screen shows which shop and which phone.

## Pages and variants
A page on the website = products with the same **page title** (Online Store → product → *Website page*). By default phones use their model (for example "Apple iPhone 18 Pro Max"), so Red 256GB in shop 1 and Blue 256GB in shop 2 are two variants of one page. Variants are phone + condition + PTA status + type + price. The page description is shared by all variants; photos are per colour/storage product.

## Website views (read-only database login)
| View | Rows |
| --- | --- |
| `web_families` | one per page: title, brand, category, description, price range, total stock, variant count, cover |
| `web_listings` | one per published product (colour/storage): page_title, family_slug, price range, stock |
| `web_variants` | one per sellable variant: product_id, condition, pta_status, device_type, price, quantity, grade, battery_health, warranty_days |
| `web_variant_stock` | the same variants split by shop: shop_id, shop_name, quantity |
| `web_stock`, `web_phone_units`, `web_images` | per product per shop; per phone (no IMEI); photos |

Grant the website's read-only role `SELECT` on **all** of these views (see the SQL above, add `web_families, web_variants, web_variant_stock`). Quantities never include phones that are reserved, sold, in transit, or accessory units held by a new order.

## Order API (website → ERP)
Set the environment variable `ONLINE_API_KEY` in Vercel to a long random secret. Without it the API is off.

`POST /api/online/orders/` with header `X-API-Key: <the secret>` and JSON:
```json
{ "external_ref": "WEB-1001",
  "customer": {"name": "Sara", "phone": "03211234567", "email": "", "address": "Street 1", "city": "Karachi"},
  "payment_method": "Cash on Delivery",          // or Bank Transfer, Easypaisa, JazzCash, Card
  "payment_status": "Paid",                      // only for a prepaid method the website has verified
  "payment_reference": "TRX-77", "delivery_fee": "300", "notes": "",
  "expected_total": "100300",
  "items": [ {"product_id": "<web_variants.product_id>", "condition": "New", "pta_status": "PTA", "device_type": "New", "price": "100000", "quantity": 1} ] }
```
* Prices are always taken from the ERP; the price you send must match the variant's current price or the order is refused (**409**, "price changed / no longer available") so the website can refresh. `expected_total` is checked the same way.
* Sending the same `external_ref` again returns the same order (**200**, `created: false`): safe to retry.
* `GET /api/online/orders/<external_ref>/` returns status (`New`, `Dispatched`, `Delivered`, `Cancelled`), courier, tracking.

## Who does what
* **Online login** (Settings → Shop accounts → *Online store logins*): sees only Online Dashboard, Orders, Online Stock, Products & Photos, online refunds. It cannot open shop money, customers, reports, settings or the POS.
* **Order flow:** New (stock reserved) → *Confirm payment* (prepaid) → **Dispatch** (invoices posted, courier/tracking) → **Delivered** (cash on delivery is received into the shop account chosen). A New order can be cancelled (stock released) or moved to another shop that holds everything.
* **Refund / exchange:** open the order → *Return / exchange* on the invoice → the normal Returns & Exchanges screen (refund, credit, or exchange). The phone returns to *Holding* in the shop that sold it. An exchange creates a replacement order that is paid by the credit and must come from the same shop.
* **Accounting:** Online Accounts shows sales, returns, money received by method, cash on delivery still to collect, cost of goods, online expenses and net profit. Tick **online-store expense** in Money Out for ads, courier, packaging and gateway fees. Everything is also in each shop's own books, so the totals always agree.
