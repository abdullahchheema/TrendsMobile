# Prompt for the website's Claude Code (paste everything below the line)

---

You are working in the **frontend repository of an e-commerce website for a Pakistani mobile-phone business**. The storefront UI already exists. Your job is to **connect it to the business's ERP and to Supabase**, and to **remove the website's own dashboard and any data it owns** (orders, stock, prices). Read this whole brief before changing anything, then inspect the repository, then propose a short plan and follow it.

## 0. Ground rules
1. **First explore, then plan.** Read the project structure, `package.json`, the router (App Router or Pages), existing data-fetching code, the cart/checkout code, the admin/dashboard code, any ORM/DB code, and `.env*` files. Write a short plan (files you will touch, files you will delete) before editing. Do not rewrite the UI design; keep components, styling and routes unless they must change.
2. **The ERP is the single source of truth** for stock, prices, discounts, photos, orders, refunds and accounting. The website stores **no** stock, price, order or finance data of its own.
3. **Secrets:** never put the ERP API key in browser code, `NEXT_PUBLIC_*` variables, logs, git, or client components. Never use the Supabase **service/secret** key. Never connect to the ERP database directly.
4. Do not invent endpoints, columns or fields. Use exactly the contracts below. If something you need is not in this brief, **stop and list the question** instead of guessing.
5. Keep TypeScript strict if the project uses it. Add tests for the pure logic you write (mapping, cart validation, error handling).

## 1. Architecture (what talks to what)
```
Browser ──► Next.js (Vercel)
              ├─ READS   ─► Supabase Data API, schema "api", PUBLIC (anon/publishable) key  → catalogue, variants, stock, prices, photos
              └─ ORDERS  ─► ERP  POST {ERP_API_URL}/api/online/orders/   header X-API-Key  (SERVER-SIDE ONLY)
                            ERP  GET  {ERP_API_URL}/api/online/orders/{external_ref}/  (order status, SERVER-SIDE ONLY)
```
Who does what in the ERP (so the storefront can match reality): when an order is accepted the ERP **reserves** the exact phones (they disappear from the catalogue immediately); the online team later dispatches, marks delivered, handles refunds/exchanges, and does all accounting. The website does none of that.

## 2. Environment variables
| Name | Where | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | public | project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public | the **publishable** key only |
| `ERP_API_URL` | server only | ERP base address, no trailing slash |
| `ERP_API_KEY` | server only | shared secret; must never reach the client bundle |

Create `.env.example` with these names and no values. Add a small `lib/env.ts` that validates them at startup (server vars only on the server).

## 3. Reading the catalogue (Supabase, schema `api`)
```ts
// lib/supabase.ts — holds only the public key, safe in server and client code
import { createClient } from '@supabase/supabase-js'
export const sb = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, { db: { schema: 'api' } })
```
The **only** readable objects are these seven views (anything else is private and will be refused):

| View | One row per | Columns |
| --- | --- | --- |
| `web_families` | a website page (phone model or accessory) | `family_slug, title, brand, category, description, specs, is_phone, price_from, price_to, discount_percent, stock_total, variant_count, cover_url` |
| `web_listings` | a colour/storage product on a page | `product_id, slug, family_slug, page_title, name, brand, model, storage, colour, category, description, specs, is_phone, discount_percent, price_from, price_to, stock_total, cover_url` |
| `web_variants` | **a sellable variant (what the customer buys)** | `product_id, condition, pta_status, device_type, price, compare_price, quantity, grade, battery_health, warranty_days` |
| `web_variant_stock` | the same variants split by shop | `product_id, condition, pta_status, device_type, price, shop_id, shop_name, quantity` |
| `web_stock` | product per shop | `product_id, shop_id, shop_name, quantity` |
| `web_phone_units` | one available phone (no IMEI) | `unit_id, product_id, shop_id, shop_name, condition, grade, battery_health, pta_status, device_type, warranty_days, price, compare_price` |
| `web_images` | a photo | `image_id, family_slug, variant_product_id, product_id, url, alt, position` |

### Meaning and mapping rules (important)
* A **page** = one phone model (e.g. "Apple iPhone 18 Pro Max") or one accessory, identified by `family_slug`. A page exists on the website **only if it has at least one photo and is not hidden** in the ERP — you never need to implement publishing; if a row is in the view, show it.
* A **product** (`web_listings` row) is one colour/storage of the page. A **variant** (`web_variants` row) is product + `condition` + `pta_status` + `device_type` + `price`. Offer these as selectors (storage, colour, condition/PTA). Phones in several shops with the same details are **already merged** into one variant with the total `quantity`.
* **Price shown** = `price` (discounts are already applied by the ERP). If `compare_price` is not null, show it crossed out as the old price (and you may show the percentage off).
* **Stock**: `quantity` is the total over all shops. `web_variant_stock` says which shop holds how many; show e.g. "5 in stock" and optionally "Available at: Shop A (3), Shop B (2)". A variant with no row is **out of stock** (it vanishes from the view the moment any shop or another online order takes the last unit) — show it disabled/"Out of stock", never as buyable.
* **Photos**: for a page use `web_images` where `family_slug = <slug>` ordered by `position`; the first is the cover. Rows with `variant_product_id = null` are shared by the whole page; a row with a `variant_product_id` belongs only to that colour/storage product — show it when that product's variant is selected, otherwise show the shared photos. Add the Supabase storage host to `images.remotePatterns` in `next.config.*`.
* `specs` is a JSON object grouped for display, for example `{ "Display": { "Screen size": "6.9 inch" }, "Camera": { ... }, "Other": { ... } }`. Render every group present as a titled specifications table (group and field names are already display labels; do **not** hard-code a field list; it may be empty for accessories). Type it as `Record<string, Record<string, string>>` and handle null.
* `grade` is the condition score out of 10; `battery_health` is a percentage (may be null); `warranty_days` may be 0 (then show "No warranty").
* `condition`, `pta_status`, `device_type` are plain strings from the ERP (e.g. "Good", "PTA", "Used"). Display them as given; do not hard-code allowed values.

### Queries to implement
```ts
// listing page
const { data: pages } = await sb.from('web_families').select('*').order('title')       // optionally .gt('stock_total', 0)

// model page
const { data: listings } = await sb.from('web_listings').select('*').eq('family_slug', slug)
const ids = (listings ?? []).map(l => l.product_id)
const [{ data: variants }, { data: byShop }, { data: photos }] = await Promise.all([
  sb.from('web_variants').select('*').in('product_id', ids),
  sb.from('web_variant_stock').select('*').in('product_id', ids),
  sb.from('web_images').select('*').eq('family_slug', slug).order('position'),
])
```
Handle `error` from every query (show a friendly state, log server-side). Use ISR: `export const revalidate = 30` on catalogue pages (a stale page can never oversell because checkout re-validates — see §4). Build typed interfaces for each view (`WebFamily`, `WebListing`, `WebVariant`, …) in one file.

Search/filter/sort on the listing page should use these views only (brand, price range from `price_from`/`price_to`, in-stock). Do not create your own copies of this data.

## 4. Cart and checkout → ERP order API
### Cart
A cart line must store exactly what the order API needs from the chosen `web_variants` row: `productId` (= `product_id`), `condition`, `pta_status`, `device_type`, `price` (as a string), `quantity`, plus display data. For **accessories** (`is_phone` false) `condition/pta_status/device_type` are empty strings — send only `product_id` and `quantity`. The same variant must be **one line with a quantity**, never two lines. Quantity must be between 1 and the variant's `quantity` and at most 50. Maximum 40 lines.

Before showing checkout and again when the customer presses "Place order", **re-read** the cart's variants from `web_variants`; if a variant is gone, its `quantity` is lower, or its `price` changed, update the cart and ask the customer to confirm. This is a convenience — the ERP is the final check.

### The order route (server-side only)
```ts
// app/api/checkout/route.ts   (or a server action)
export async function POST(req: Request) {
  const b = await req.json()                       // validate with zod (or equivalent) first
  const res = await fetch(`${process.env.ERP_API_URL}/api/online/orders/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': process.env.ERP_API_KEY! },
    cache: 'no-store',
    body: JSON.stringify({
      external_ref: b.orderId,                      // your own unique order number; the SAME value on every retry
      customer: { name: b.name, phone: b.phone, alt_phone: b.altPhone, email: b.email, address: b.address, city: b.city, postal_code: b.postalCode },   // extra customer keys are stored too
      payment_method: b.paymentMethod,
      payment_status: b.paid ? 'Paid' : undefined,  // only for a prepaid method the website has itself verified
      payment_reference: b.paymentRef,
      delivery_fee: b.deliveryFee,                  // the delivery charge the customer saw, in PKR
      expected_total: b.total,                      // items + delivery, as the customer saw it
      notes: b.notes,
      discount: b.coupon ? { amount: b.couponAmount, code: b.coupon } : undefined,   // coupon the website applied, PKR; expected_total must already include it
      shipping: { method: b.shippingMethod },
      items: b.items.map((i: any) => ({
        product_id: i.productId, condition: i.condition, pta_status: i.pta, device_type: i.type,
        price: String(i.price), quantity: i.qty,
      })),
    }),
  })
  return Response.json(await res.json(), { status: res.status })
}
```
**Field rules**
* `external_ref`: generate a unique id when the checkout page opens (e.g. UUID or `WEB-` + timestamp+random) and **reuse it for retries/double-clicks**; sending it again returns the same order (`created: false`) — never create a new id for a retry of the same cart.
* `payment_method` must be exactly one of: `Cash on Delivery`, `Bank Transfer`, `Easypaisa`, `JazzCash`, `Card`.
* `payment_status: "Paid"` only for a prepaid method **the website itself verified** (e.g. a gateway callback). For `Cash on Delivery` never send it. If a prepaid order is sent unpaid, the ERP team confirms the payment manually before dispatch.
* `customer.name` and `customer.phone` are required; phone needs at least 10 digits (accept `0300 1234567` and `+92 300 1234567`); validate in the form with clear messages.
* Send **all order details the website has**: any additional top-level field (e.g. `billing_address`, `source`, `utm`, `gift_message`, `delivery_notes`) is stored with the order exactly as received and shown to the online team (about 20 KB limit). `expected_total = items - discount + delivery_fee`.
* Prices and totals are **always recomputed by the ERP** from its own data; what you send is only compared against them (use `String(price)`, e.g. `"108000.00"` or `"108000"`).

**Responses to handle**
| Status | Meaning | Website behaviour |
| --- | --- | --- |
| `201` | order created, stock reserved; body `{ order, external_ref, status, subtotal, delivery_fee, total, created: true }` | clear the cart, show the confirmation page with the order number (`order`) and total |
| `200` + `created: false` | the same `external_ref` was already accepted | treat as success |
| `409` | price changed / variant sold out / not enough stock / total differs; body `{ error }` | refresh the cart from `web_variants`, show what changed, let the customer confirm again (use a **new** `external_ref` only if the cart content changed) |
| `400` | validation problem (missing name/phone, bad payment method, …); body `{ error }` | show the message next to the form |
| `401` | wrong/missing API key or API disabled | show a generic "try again later", log loudly server-side, alert the owner |
| network error / 5xx | unknown outcome | **do not** create a new order id; offer "retry" with the same `external_ref` (safe) |

Never call this endpoint from the browser. Never log the API key or full customer details.

### Order status (tracking page)
`GET {ERP_API_URL}/api/online/orders/{external_ref}/` with the same `X-API-Key` header → `{ order, external_ref, status ("New" | "Dispatched" | "Delivered" | "Cancelled"), payment_status ("Unpaid" | "Paid"), courier, tracking, total, placed_on, dispatched_on, delivered_on }`. Implement a server route that proxies this (key stays server-side) and a tracking page that asks for the order number (and the phone number, which you must check against your own record before revealing anything — the ERP endpoint does not check the phone).

## 5. What to REMOVE
* The website's own **dashboard/admin area** and its routes, components, API routes and auth for admins.
* Any **database tables, ORM models, migrations, seed data, API routes** that store orders, stock, prices, products or inventory. Orders and stock live only in the ERP.
* Any code that computes prices, discounts or availability itself — display what the views return.
* Anything that talks to a DB other than Supabase-through-the-public-key.
Before deleting, list what you will remove and confirm nothing else depends on it (search imports). Keep customer-facing pages, the design system and unrelated features (e.g. static pages).

## 6. Customer accounts (only if the website already has them)
Do not put customer data in the ERP's schema. If the site needs customer logins, wishlists, reviews, carts or contact messages, ask the owner before creating anything: those tables must live in a **separate schema or project owned by the website**, with row-level security so a visitor sees only their own rows. Do not change Supabase Auth settings in this project (it also holds the ERP's staff logins). Orders are never stored on the website side except as the `external_ref` you may keep to look the order up.

## 7. UX requirements
* Model page: gallery (shared photos, switching to the selected variant's photos when it has its own), title, brand, description, selectors for storage/colour/condition built from the variants, price with strike-through `compare_price` and "% off" when present, stock indicator ("Only 2 left" when `quantity` ≤ 2, "Out of stock" when absent), battery health/grade/PTA/warranty chips for phones.
* Out-of-stock combinations are visibly disabled; the "Add to cart" button is disabled unless a specific variant is selected.
* Cart: quantity limited to the variant's stock; clear messages when stock/price changed.
* Checkout: customer form, city, address, payment method radio (the five allowed values), delivery fee displayed, total, single "Place order" button that disables while sending (no double submit).
* Mobile first; accessible (labels, focus states, alt text from `web_images.alt`).
* Currency format: PKR with thousands separators and no decimals unless non-zero.

## 8. Quality, security and tests
* Validate all request bodies on the server (zod or similar); never trust client prices — they are only "expected" values.
* Rate-limit the checkout route (e.g. per IP) and add a honeypot/captcha if the site already uses one.
* No `console.log` of secrets or personal data. Handle all error branches above.
* Add unit tests for: variant → cart-line mapping, cart revalidation (price changed / sold out), the 201/200/409/400/401/network handling, price/compare formatting. Add a build check that fails if `ERP_API_KEY` appears in the client bundle (search `.next/static` for the key name or a placeholder).
* Run lint, type-check and the production build; fix everything you touch.

## 9. Acceptance checklist (run these yourself and report the results)
1. `GET {SUPABASE_URL}/rest/v1/web_families?select=*` with headers `apikey: <anon>` and `Accept-Profile: api` returns rows for pages that have photos (an empty list means no page has a photo yet — not a bug).
2. A request for any other table (e.g. `shop_device`) is refused. The website code reads only the seven views.
3. The listing and model pages render from the views; selecting a variant updates price, compare price, stock and photos; out-of-stock combinations are disabled.
4. A Cash on Delivery order returns `201`; sending the same `external_ref` again returns `200` with `created: false` and no duplicate.
5. A wrong price or an over-large quantity returns `409` and the UI refreshes the cart with a clear message.
6. The tracking page shows `status`, `courier`, `tracking` for a known order.
7. No admin routes, order/stock/price tables, or price logic remain; `ERP_API_KEY` is absent from the browser bundle and from git.
8. Lint, type-check, tests and `next build` all pass.

## 10. Output I want from you
First reply with your **plan** (files to add, change, delete, and any open questions). After I approve, implement in small, reviewable commits (catalogue reading → cart → checkout route → tracking → removal of the old dashboard/data → tests), and finish with a short report: what changed, what you deleted, how to run it, test results, and anything that needs the ERP owner to change (never assume an ERP change; ask).

If anything in this brief conflicts with what you find in the repository, **stop and ask** rather than guessing.
