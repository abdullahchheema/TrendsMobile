# Online store: how the website, the stock and the books fit together

The ERP is the control room for the website. Photos, prices, discounts, orders and online accounting are managed here; the website only reads and places orders.

## 1. The catalogue (Products & Photos)
* **One line per brand and model**, with its variants (storage, colour, condition) and **where each variant is in stock** (every shop, with quantities). Stock of identical phones in several shops is one variant with the total.
* **Photos are added here only.** Add a photo to a model and the whole page goes **live** on the website. A page with no photo exists only as a **draft** here. The owner can also hide a page. A new colour added to a page that already has photos goes live by itself.
* Photos are for the page (shared by every variant) or for one colour/storage only. JPG, PNG or WebP, 5 MB, 12 per page; big photos are resized in the browser. Stored in the public Supabase bucket `product-images` (created on the first upload).
* **Prices:** the shop price (set when stock is bought; the POS uses it) is never changed here. Per variant you can set an **online price**, a **percentage off** and a **compare price** (the crossed-out "was" price). Price the customer pays = online price (or shop price) minus the percentage off; the compare price must be higher than that. "Percentage off for every variant" puts the whole page on sale in one step.

## 2. Accounting: the Online Store is its own set of books
Each shop has its own books, so online sales cannot simply be posted in whichever shop held the phone. The Online Store therefore has **its own books** (its own cash, bank and wallets, customers, sales, refunds and expenses) and **holds no stock**:

| Event | Online Store | The shop that held the phone |
| --- | --- | --- |
| Online sale | Dr Cash/Receivable, Cr Sales (customer's price); Dr Cost of goods sold, Cr Inter-shop clearing (the shop's cost) | Dr Inter-shop clearing, Cr Inventory (the same cost) |
| Online return into any shop | reverses the sale and the cost of goods; Dr Inter-shop clearing | Dr Inventory, Cr Inter-shop clearing (the shop that receives the phone) |
| Online Store pays a shop | Dr Inter-shop clearing, Cr its bank/cash | Dr its bank/cash, Cr Inter-shop clearing |

* Phones move at **cost**, so shop profit is not distorted; the online margin shows in the Online Store's books. The two sides always cancel out in the owner's consolidated view (Online Accounts shows "the books agree").
* Money received online lands in the **Online Store's** accounts, not in a shop. COD collected on delivery goes to the Online Store.
* **Pay a shop** (Online Accounts, owner only) moves real money from an Online Store account to a shop account and clears what is owed; it can be paid in parts and never more than owed or more than the account holds.
* **Online expenses** (ads, courier, packaging, gateway fees) and **money put in by the owner** are recorded in Online Accounts, in the Online Store's books.
* **Shops know nothing about online:** online customers, invoices and money are not in any shop's lists or reports. A shop's books show only the stock leaving at cost and a claim on the Online Store (inter-shop clearing). The **owner sees everything**: every shop, the Online Store, and all sales physical and online (Sales History marks online sales; reports and dashboard include the Online Store).

## 3. Orders
* A website order arrives through the API and **reserves stock** at once (phones become *Reserved*; accessory units are held back), from whichever shops hold it: one shop if it can supply everything, otherwise split. The order page shows the shop and the exact phones (IMEI); a New order can be moved to another shop that holds everything.
* **Dispatch** posts one invoice in the Online Store's books; the stock leaves the shops. **Delivered** collects cash on delivery into the Online Store.
* A phone sold in a shop disappears from the website at once; an online order takes it out of the shop's stock.
* **Refund / exchange:** on the order, *Return / exchange* on the invoice. Choose which **shop takes the stock back** (default: the shop it came from). The phone goes to *Holding* there; the refund comes out of the Online Store's money. An exchange creates a replacement order paid by the credit and can be supplied by any shop.
* The online-store **login** (Settings → Shop accounts → Online store logins) sees only these screens. Shops cannot see them.

## 4. Order API (website → ERP)
Set `ONLINE_API_KEY` in Vercel to a long random secret (without it the API is off).

`POST /api/online/orders/` with header `X-API-Key: <the secret>` and JSON:
```json
{ "external_ref": "WEB-1001",
  "customer": {"name": "Sara", "phone": "03211234567", "email": "", "address": "Street 1", "city": "Karachi"},
  "payment_method": "Cash on Delivery",
  "payment_status": "Paid",
  "payment_reference": "TRX-77", "delivery_fee": "300", "notes": "", "expected_total": "100300",
  "items": [ {"product_id": "<web_variants.product_id>", "condition": "New", "pta_status": "PTA", "device_type": "New", "price": "100000", "quantity": 1} ] }
```
`payment_method`: Cash on Delivery, Bank Transfer, Easypaisa, JazzCash or Card (`payment_status: Paid` only for a prepaid method the website verified). Prices always come from the ERP; the price sent must match the variant's current price (the discounted price) or the order is refused with **409**. Re-sending the same `external_ref` returns the same order (safe to retry). `GET /api/online/orders/<external_ref>/` returns status, courier and tracking.

## 5. Read-only database views for the website
| View | Rows |
| --- | --- |
| `web_families` | one per page: title, brand, category, description, price range, discount_percent, total stock, variant count, cover |
| `web_listings` | one per colour/storage: page_title, family_slug, price range, stock |
| `web_variants` | one per sellable variant: product_id, condition, pta_status, device_type, **price**, **compare_price**, quantity, grade, battery_health, warranty_days |
| `web_variant_stock` | the same variants split by shop |
| `web_stock`, `web_phone_units`, `web_images` | per product per shop; per phone (no IMEI); photos (page-wide, or `variant_product_id` for one variant) |

They contain no cost, IMEI, supplier or customer data. Quantities exclude phones that are reserved, sold or in transit.

**Where they live.** The ERP's tables and these views are in the private `covison_app` schema, which the Supabase Data API and the `anon` key cannot reach. To let the website read them, run `supabase/hosted/web-views.sql` once in the Supabase SQL editor (after `update-database.ps1`). It creates read-only copies in the `api` schema and grants `SELECT` on them to `anon`. In Supabase, Project Settings → Data API → *Exposed schemas* must include `api`. The website then reads them with `createClient(url, anonKey, { db: { schema: 'api' } })`.

If a later ERP migration drops and recreates the `covison_app.web_*` views, drop the `api.web_*` views first (or re-run `web-views.sql` afterwards); the release notes will say so.
