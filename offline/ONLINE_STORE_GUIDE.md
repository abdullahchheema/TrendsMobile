# Online store: how the website, the stock and the books fit together

The ERP is the control room for the website. Photos, prices, discounts, orders and online accounting are managed here; the website only reads and places orders.

## 1. The catalogue (Products & Photos)
* **One line per brand and model**, with its variants (storage, colour, condition) and **where each variant is in stock** (every shop, with quantities). Stock of identical phones in several shops is one variant with the total.
* **A page goes live when it has a photo and, for phones, its required specifications** (screen size, chipset, RAM, main camera, battery, operating system). Until then it is a **draft** here; the page shows what is still missing. The owner can also hide a page.
* **Specifications:** about 40 fields in groups (Display, Performance, Camera, Battery, Connectivity, Body, In the box) plus your own extra lines. They are saved once per model, shared by every variant, and reach the website in the `specs` column. A new colour added to a page that already has photos goes live by itself.
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
* **Payment confirmed** (prepaid methods): the money is booked into the Online Store's account at once, as the customer's advance (not a sale).
* **Dispatch** records the courier and tracking number. Nothing is posted: the phones stay in their shop's stock, still *Reserved*, until the customer has them.
* **Delivered** posts the sale: one invoice in the Online Store's books, the stock leaves the shops at cost and each shop is owed that cost. Cash on delivery is collected into the Online Store; a prepaid order uses its advance. (Online revenue is recognised at delivery: owner's decision, 14 Nov 2026.)
* A phone sold in a shop disappears from the website at once; an online order takes it out of the shop's stock.
* **Refund / exchange:** on the order, *Return / exchange* on the invoice. Choose which **shop takes the stock back** (default: the shop it came from). The phone goes to *Holding* there; the refund comes out of the Online Store's money. An exchange creates a replacement order paid by the credit and can be supplied by any shop.
* **A parcel that was not delivered** (*Delivery failed*, then *Receive returned parcel*): nothing was sold, so nothing is reversed. The phones go back on the shelf of the shop that sent them, at their original cost (*Holding*, *Available* or *damaged*, by the inspection result); move them later with a stock transfer if needed. For cash on delivery the customer owes nothing. For a prepaid order the delivery that was paid for and attempted is kept (a small invoice for the delivery charge, paid from the advance) and the rest of the advance is owed back: refund it from Online Finance, Money Out, *Refund a customer*.
* **A paid order that is cancelled** keeps its money as the customer's advance; the order history says it is owed back. Refund it the same way.
* **Accessory units held for online orders stay on the shelf:** a shop cannot sell them, write them off, send them back to the supplier, cancel their purchase or move them while a new online order holds them. The message says how many are held; cancel the order or supply it from another shop first.
* **One customer, one account:** `0300 1234567` and `+92 300 1234567` are the same online customer (matched on the last ten digits).
* **Check at any time:** `python manage.py online_check` (orders against stock, including a phone that is Reserved with nothing holding it) and `python manage.py verify_books` (every books check, including that an empty accessory shelf carries no value).
* The online-store **login** (Settings → Shop accounts → Online store logins) sees only these screens. Shops cannot see them.

## 4. Order API (website → ERP)
Set `ONLINE_API_KEY` in Vercel to a long random secret (without it the API is off).

`POST /api/online/orders/` with header `X-API-Key: <the secret>` and JSON:
```json
{ "external_ref": "WEB-1001",
  "customer": {"name": "Sara", "phone": "03211234567", "email": "", "address": "Street 1", "city": "Karachi"},
  "payment_method": "Cash on Delivery",
  "payment_reference": "TRX-77", "delivery_fee": "300", "notes": "", "expected_total": "100300",
  "items": [ {"product_id": "<web_variants.product_id>", "condition": "New", "pta_status": "PTA", "device_type": "New", "price": "100000", "quantity": 1} ] }
```
`payment_method`: Cash on Delivery, Bank Transfer, Easypaisa, JazzCash or Card. A prepaid order always arrives unpaid: the online team confirms the payment on the order page, which books the money (a `payment_status` sent by the website is ignored since 14 Nov 2026). Prices always come from the ERP; the price sent must match the variant's current price (the discounted price) or the order is refused with **409**. Re-sending the same `external_ref` returns the same order (safe to retry). `GET /api/online/orders/<external_ref>/` returns status, courier and tracking.

## 5. Read-only database views for the website
| View | Rows |
| --- | --- |
| `web_families` | one per page: title, brand, category, description, `specs`, price range, discount_percent, total stock, variant count, cover |
| `web_listings` | one per colour/storage: page_title, family_slug, `specs`, price range, stock |
| `web_variants` | one per sellable variant: product_id, condition, pta_status, device_type, **price**, **compare_price**, quantity, grade, battery_health, warranty_days |
| `web_variant_stock` | the same variants split by shop |
| `web_stock`, `web_phone_units`, `web_images` | per product per shop; per phone (no IMEI); photos (page-wide, or `variant_product_id` for one variant) |

They contain no cost, IMEI, supplier or customer data. Quantities exclude phones that are reserved, sold or in transit.

**Where they live.** The ERP's tables and these views are in the private `covison_app` schema, which the Supabase Data API and the `anon` key cannot reach. To let the website read them, run `supabase/hosted/web-views.sql` once in the Supabase SQL editor (after `update-database.ps1`). It creates read-only copies in the `api` schema and grants `SELECT` on them to `anon`. In Supabase, Project Settings → Data API → *Exposed schemas* must include `api`. The website then reads them with `createClient(url, anonKey, { db: { schema: 'api' } })`.

**When a release changes these views** (the release notes say so; this release does, because it adds `specs`): run `supabase/hosted/web-views-drop.sql` in the SQL editor first, then `update-database.ps1`, then `supabase/hosted/web-views.sql` again. The update stops with a clear message if the first step was forgotten. The website cannot read the catalogue for the minute in between.

## 6. Online finance (the same screens a shop has)
Online Finance (owner and online login): Money In, Money Out, Online Expenses, Customer Dues (cash on delivery still to collect), Cash and Bank (including moving money between the Online Store's own accounts), Transaction History, and Online Accounts & Profit (sales, returns, profit, what is owed to each shop, paying a shop). All of it is the Online Store's own books; shops cannot open these screens.

## 7. What an order carries
Everything the website sends is kept and shown on the order: customer name, phone, other phone, email, address, city, postal code, shipping method, coupon discount and code, payment method and reference, delivery charge, notes, and any other field (billing address, source, campaign, ...) exactly as received. A coupon discount is applied to the invoice, so the books match what the customer paid.

## 8. Demo data
`supabase/hosted/seed-online-store.ps1` (asks only for the database password) gives phones that have no selling price a price of cost + 15 % (rounded to PKR 500), fills typical specifications for the demo models, and adds generated illustrations as photos so the pages are live. It never overwrites anything that already exists. Replace the illustrations with real photos at any time.
