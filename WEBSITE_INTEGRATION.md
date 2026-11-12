# Website ↔ ERP integration guide (for the website developer)

The ERP (Django, on Vercel) is the single source of truth for **stock, prices, photos, orders, refunds and accounting**. The website is only a storefront:

* **Reads** the catalogue from Supabase through read-only views (public key, schema `api`).
* **Places orders** by calling the ERP's order API from a **server-side** route (secret key).
* Owns **no** stock, price, order or finance data. Delete the website's own dashboard and its order/stock/price tables.

```
Browser ──► Next.js (Vercel)
              ├─ reads  ─► Supabase Data API  (schema "api", anon key)       catalogue, stock, photos, prices
              └─ orders ─► ERP  POST /api/online/orders/  (X-API-Key)          reserve stock, create order
```

## 1. Keys and addresses (the owner fills these in; never commit them)

| Variable (website Vercel project) | Value | Public? |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://xsxskhcqejutouhlallh.supabase.co` | yes |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | publishable key (`sb_publishable_ySNqZW4cffaEDAn10RdkPg_CP5BIZ51`): **`____________`** | yes |
| `ERP_API_URL` | the ERP's address, e.g. `https://trends-mobile.vercel.app` (no trailing slash) | no |
| `ERP_API_KEY` | secret shared with the ERP (`ONLINE_API_KEY` there): **`____________`** | **NO** — server only, never `NEXT_PUBLIC_`, never in browser code or git |

Rules: `ERP_API_KEY` is used only in server code (route handlers / server actions). Never put the Supabase **secret/service** key in the website. The ERP's database login is never given to the website.

## 2. Reading the catalogue (Supabase, schema `api`)

```ts
// lib/supabase.ts  (works in server and client components: it only holds the public key)
import { createClient } from '@supabase/supabase-js'
export const sb = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  { db: { schema: 'api' } }            // IMPORTANT: the views are in "api", not "public"
)
```

Only the seven views below are readable. Everything else is private. A model appears **only when it has at least one photo and is not hidden** in the ERP; with no photo it is a draft and does not exist for the website.

| View | One row per | Columns |
| --- | --- | --- |
| `web_families` | page (phone model / accessory) | `family_slug, title, brand, category, description, specs, is_phone, price_from, price_to, discount_percent, stock_total, variant_count, cover_url` |
| `web_listings` | colour/storage product on a page | `product_id, slug, family_slug, page_title, name, brand, model, storage, colour, category, description, specs, is_phone, discount_percent, price_from, price_to, stock_total, cover_url` |
| `web_variants` | **sellable variant** (what a customer buys) | `product_id, condition, pta_status, device_type, price, compare_price, quantity, grade, battery_health, warranty_days` |
| `web_variant_stock` | the same variants split **by shop** | `product_id, condition, pta_status, device_type, price, shop_id, shop_name, quantity` |
| `web_stock` | product per shop | `product_id, shop_id, shop_name, quantity` |
| `web_phone_units` | one available phone (no IMEI) | `unit_id, product_id, shop_id, shop_name, condition, grade, battery_health, pta_status, device_type, warranty_days, price, compare_price` |
| `web_images` | photo | `image_id, family_slug, variant_product_id, product_id, url, alt, position` |

### How the data maps to the storefront
* **Listing page:** `web_families` → one card per model (`title`, `brand`, `cover_url`, `price_from`–`price_to`, `stock_total`). Filter `stock_total > 0` to hide sold-out pages (or show them as "Out of stock").
* **Model page** (`family_slug`): the model's colour/storage products are in `web_listings` (`storage`, `colour`, `product_id`). Their sellable variants are in `web_variants` (join on `product_id`). A variant = product + `condition` + `pta_status` + `device_type` + `price`. Offer storage/colour/condition as the option selectors.
* **Price shown to the customer:** `price` (discounts already applied by the ERP). If `compare_price` is not null, show it crossed out ("was").
* **Stock:** `quantity` per variant is the total over all shops. `web_variant_stock` tells you which shop holds how many (show "3 in stock" or "Available at Shop A"). Identical phones in several shops are already summed into one variant.
* **Photos:** `web_images` for the page (`family_slug`). Rows with `variant_product_id = null` belong to the whole page; a row with a `variant_product_id` belongs to that colour/storage only (show it when that variant is selected). Order by `position`; the first is the cover. Add the Supabase host to `images.remotePatterns` in `next.config.js` so the photo URLs load.
* **Specifications:** `specs` (on `web_families` and `web_listings`) is a JSON object grouped for display: `{ "Display": { "Screen size": "6.9 inch" }, "Camera": { ... }, "Other": { "Launch year": "2025" } }`. Render each group as a titled table; group and field names are already display labels. It can be empty for accessories. Never hard-code the field list: show whatever is present.
* **Out of stock** = the variant row disappears from `web_variants` (a phone sold in any shop or reserved by another order is removed at once).

```ts
// model page
const { data: listings } = await sb.from('web_listings').select('*').eq('family_slug', slug)
const ids = (listings ?? []).map(l => l.product_id)
const [{ data: variants }, { data: byShop }, { data: photos }] = await Promise.all([
  sb.from('web_variants').select('*').in('product_id', ids),
  sb.from('web_variant_stock').select('*').in('product_id', ids),
  sb.from('web_images').select('*').eq('family_slug', slug).order('position'),
])
```

**Freshness:** use `export const revalidate = 30` (or `fetch(..., { next: { revalidate: 30 } })`) on catalogue pages. A stale page can never oversell, because the order API re-checks stock and price (see §3).

## 3. Placing an order (server route → ERP)

```ts
// app/api/checkout/route.ts   (server only)
export async function POST(req: Request) {
  const b = await req.json()
  const r = await fetch(`${process.env.ERP_API_URL}/api/online/orders/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': process.env.ERP_API_KEY! },
    body: JSON.stringify({
      external_ref: b.orderId,                      // YOUR order number; stable across retries
      customer: { name: b.name, phone: b.phone, alt_phone: b.altPhone, email: b.email, address: b.address, city: b.city, postal_code: b.postalCode },   // any extra customer keys are stored too
      payment_method: b.paymentMethod,              // see list below
      payment_status: b.paid ? 'Paid' : undefined,  // only for prepaid methods you have verified
      payment_reference: b.paymentRef,
      delivery_fee: b.deliveryFee,
      expected_total: b.total,                      // what the customer saw (items + delivery)
      notes: b.notes,
      discount: b.coupon ? { amount: b.couponAmount, code: b.coupon } : undefined,   // order-level coupon the website applied (PKR)
      shipping: { method: b.shippingMethod },
      items: b.items.map((i: any) => ({
        product_id: i.productId,                    // web_variants.product_id
        condition: i.condition, pta_status: i.pta, device_type: i.type,   // phones: copy from the variant row
        price: String(i.price),                     // web_variants.price (the discounted price)
        quantity: i.qty,
      })),
    }),
  })
  return Response.json(await r.json(), { status: r.status })
}
```

**Fields**
* `payment_method`: `Cash on Delivery`, `Bank Transfer`, `Easypaisa`, `JazzCash` or `Card`. `payment_status: "Paid"` only for a prepaid method the website has verified (the ERP then lets the team dispatch; for unpaid prepaid orders the team confirms payment manually). Cash on Delivery is collected on delivery.
* Phones: send `condition`, `pta_status`, `device_type`, `price` exactly as in the `web_variants` row. Accessories: only `product_id`, `quantity` (price optional; if sent it must match).
* Rules: customer `name` and `phone` (≥ 10 digits) required; 1–50 per line, up to 40 lines; the same variant twice → combine into one line with a quantity. `delivery_fee` 0–100000.

**Responses**
| Status | Meaning | What the website does |
| --- | --- | --- |
| `201` | Order created; stock reserved in the shops. Body: `order, external_ref, status, subtotal, delivery_fee, total, created: true` | show confirmation |
| `200` with `created: false` | same `external_ref` sent again — the existing order | treat as success (safe to retry, double-clicks are harmless) |
| `409` | price changed / variant sold out / not enough stock / total differs | re-read the catalogue, update the cart, ask the customer to confirm again |
| `400` | validation (missing name/phone, bad method, …) — `error` has the message | show the message |
| `401` | wrong or missing API key (or API switched off in the ERP) | fix the key |

Prices and totals are always recomputed by the ERP from its own data; the website's numbers are only compared against them.

**Everything else is kept too.** Any additional top-level field (for example `billing_address`, `source`, `utm`, `gift_message`, `delivery_notes`) and any extra key inside `customer` or `shipping` is stored with the order exactly as received and shown to the online team (limit about 20 KB). `discount.amount` is deducted from the items total and `expected_total` must include it: `expected_total = items - discount + delivery_fee`.

**Order status for tracking pages** — `GET {ERP_API_URL}/api/online/orders/{external_ref}/` with the same `X-API-Key` header:
`{ order, external_ref, status: "New|Dispatched|Delivered|Cancelled", payment_status, courier, tracking, total, placed_on, dispatched_on, delivered_on }`.

## 4. What the ERP does with an order (so the storefront matches reality)
1. Order arrives → the exact phones are **reserved** (removed from the website and from the shops' POS); accessories are held back. Stock may come from several shops; the website never needs to know which.
2. The online team dispatches it (courier, tracking), later marks it delivered; cash on delivery is collected then.
3. Refunds / returns / exchanges are handled **in the ERP** by the online team; the returned phone goes back into the chosen shop's stock automatically.
4. Accounting is done in the ERP (the Online Store has its own books). The website does nothing for finance.

## 5. Checklist before launch
- [ ] `select` via the anon key works: `GET {SUPABASE_URL}/rest/v1/web_families?select=*` with headers `apikey: <anon>` and `Accept-Profile: api` (an empty list is fine until a model has a photo).
- [ ] A direct query to anything else (e.g. `shop_device`) is refused.
- [ ] A Cash on Delivery test order returns `201`; it appears in the ERP under **Online Orders**; the phone is gone from the website.
- [ ] Sending the same `external_ref` twice returns the same order (`created: false`).
- [ ] A wrong `price` returns `409`; the page shows the refreshed price.
- [ ] `ERP_API_KEY` does not appear in the browser bundle (search the built JS), and is not committed.
- [ ] The website's old dashboard, order tables and admin routes are removed.

## 6. Do not
* Do not connect the website to the ERP database or use the Supabase secret key.
* Do not store orders, stock or prices in the website; always read them from the views.
* Do not trust browser-side prices: send them only as the "expected" values the ERP compares.
* Do not call the order API from the browser.
