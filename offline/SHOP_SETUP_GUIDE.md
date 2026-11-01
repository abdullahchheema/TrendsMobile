# Four-shop setup

The setup tools are available in the separate Supabase + Django preview at **http://10.202.44.209:8081/setup/**. Actual shop and staff records are pending the client's details. Existing imported records have been preserved.

## Owner workflow

1. Sign in with the owner account and open **Four-shop setup**.
2. Choose **Add shop**. Enter a unique shop code, actual shop name, company/business name, contact and location. Choose the initial warehouse/till names and payment methods.
3. Save. The system creates an empty shop, warehouse, till, seven posting accounts and separate accounts for the selected payment methods. It creates no purchases, sales, stock movements or opening money.
4. Use **Contact & receipt settings** to enter the actual address, phone and agreed receipt/warranty text.
5. Use **Add staff** to assign names, roles and default warehouses. Create staff first, then use **Staff logins** to link their local Supabase login accounts.
6. Review **Posting accounts & payments**. Common methods include Cash, Bank Transfer, Card, JazzCash and Easypaisa. Account configuration records payments; it does not connect to an external payment provider.

Only the owner can create shops or edit posting/payment account mappings. A shop manager can maintain staff, warehouses, tills and contact/receipt settings within their own shop.

## Roles

| Preset | Responsibility |
|---|---|
| Shop manager | Manage the assigned shop, buy, sell and handle payments |
| Sales & collections | Sell and collect payments |
| Sales | Record sales; payment handling requires an authorized collector |
| Purchases | Buy and inspect devices |
| Purchases & supplier payments | Buy and handle payments |
| Cashier | Collect/pay money and complete checkout attributed to a separate seller |
| Technician | Inventory/warranty access without buying, selling or payment authority |
| Custom | Owner/manager chooses the individual responsibility flags |

Presets apply within the assigned shop. The global owner retains access across shops. Staff names on transactions remain separate from the login that recorded the action.

Any payment-handling role can complete checkout attributed to an authorized seller. The `can sell` responsibility determines whether that employee is selectable as the seller; buying/payment responsibilities are configured independently.

A default warehouse must be active and belong to the staff member's shop. It prefills transaction forms. Close an employee's open shift before changing their shop, login or payment access.

## Shared information and separation

All four shops use the shared local server. Product models/variants, customer and supplier contact records are shared. Stock, transactions, payments, tills and staff permissions remain scoped to shops. The owner can view combined reports and select a shop; shop staff see their permitted data.

Identical staff names are separated by staff identity and shop in reports. Identically named warehouses are separated by warehouse identity and shop. Shop/warehouse/account codes help distinguish records in selectors and exports.

The existing stock-transfer action operates within a shop. A dedicated inter-shop transfer workflow still needs validation before using it to move inventory between shops. Connectivity between the actual premises remains part of the network/hardware checks.

## Financial setup protections

- Cash must map to an active Cash account. Bank/card/wallet methods must map to active Bank accounts. Method names must be unique and Cash must remain configured.
- Existing posting accounts cannot change after the shop has posted transactions; a reviewed accounting migration is required.
- Close open POS shifts before changing account/payment mappings.
- An account used by a shop or posted transactions retains its code, account type and active status. Its descriptive name can be maintained.
- Warehouses with stock history, devices or staff defaults cannot move to another shop. Clear remaining stock and staff defaults before disabling a warehouse.
- Close a shop's shifts before disabling it. The owner can reopen disabled shops from the **Shops** list.

These controls preserve existing postings and prevent configuration changes from reallocating old stock or cash.

## Setup checks

The setup page checks active warehouses/tills, valid account mappings, named staff for buying/selling/payments and active operator logins with the necessary access. With Supabase enabled, a linked Supabase identity is required. A technician-only login does not qualify for trading duties.

“Setup checks complete” describes configuration. Actual sign-in, permissions, printer/scanner behavior, stock/cash reconciliation and shop-computer operation still need acceptance tests. The remaining feature-parity items in the Step 1 report remain open.

## Information needed to configure the actual shops

| Shop | Actual name / location | Contact | Business/company name | Payment methods |
|---|---|---|---|---|
| 1 | Pending | Pending | Pending | Pending |
| 2 | Pending | Pending | Pending | Pending |
| 3 | Pending | Pending | Pending | Pending |
| 4 | Pending | Pending | Pending | Pending |

For each staff member, provide their name, assigned shop, role and login email/identifier. Also identify whether the existing **trends mobile** records belong to one of these shops or should remain separate demo data. No existing stock, balances or staff are redistributed automatically.
