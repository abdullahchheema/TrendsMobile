# Stock transfers between shops

In the Supabase preview, the owner can now move phones and accessories between active shops in the same business. Shop managers retain access to transfers between warehouses in their own shop.

## Prepare each shop

Use **Shop setup → Posting accounts & payments**. Keep the existing seven posting accounts and payment methods intact. Create a separate **Current Asset** account through **Create an account**, then select it as **Inter-shop clearing (optional)** for each participating shop. The inventory mapping must use an active **Stock** account.

Clearing is optional for ordinary trading and same-shop transfers, but required on both sides of an inter-shop transfer. It cannot reuse an inventory, expense, sales, cash, bank or party account. Once posted to, the clearing mapping is locked. Adding this optional mapping does not rewrite old postings or unlock existing trading-account mappings.

No actual additional shops, staff or clearing mappings were created during implementation. Client shop details remain pending. Test locations and accounts were created only in disposable databases and removed afterwards.

## Complete a physical transfer

1. Sign in as the owner and open **Stock → Stock transfers → New**.
2. Select the source shop, posting date, source warehouse and destination warehouse. Each warehouse label identifies its shop and code.
3. Add each exact phone once with quantity one, or enter the accessory quantity. A line's warehouse must be the selected source warehouse; leaving it blank uses that source. Cost comes from existing stock, so no selling price is entered.
4. Enter the reason and any handover details. Save the draft and check the route, quantities and IMEIs.
5. Submit only when the physical move is complete. Submission immediately moves ownership/location and records the accounting entries. It is not a dispatch or an in-transit reservation.

Both shops must be active and belong to the same business/company. The accounting date must be open in both shops. Unavailable phones, duplicate phones, services, inactive warehouses and insufficient stock are rejected. If any line/account/date validation fails, the entire transfer rolls back.

Phone IMEIs, PTA details, condition, purchase cost, pricing and original received date stay attached to the same device. The destination becomes its current shop/warehouse. Its original acquisition and posted history stay intact. The completed record stores the route, IMEI snapshot, stock value, login/staff attribution and completion time. Destination managers can read incoming completed transfers; unrelated shops cannot. Normal sales staff cannot open transfer cost records.

## Accounting and reports

For PKR 50,000 of stock moved from A to B:

| Shop | Debit | Credit |
| --- | --- | --- |
| A | Inter-shop clearing 50,000 | Inventory 50,000 |
| B | Inventory 50,000 | Inter-shop clearing 50,000 |

Each shop's entry balances. The company-wide stock quantity/value remains unchanged; each shop's location balance changes. A same-shop warehouse transfer has stock movements only. No sales income, cost of goods sold, customer/supplier balance, cash receipt or payment is created by a transfer.

Check **Mobile Inventory**, **Stock Balance** and **General Ledger** with the relevant shop filter. General Ledger now identifies the shop for each row. For accessories, partial quantities use weighted stock value; moving the final quantity also moves the remaining valuation paisa, avoiding a zero-quantity balance with leftover value.

Submitted transfers cannot be cancelled in place. After checking current stock and open accounting dates, record a new transfer in the opposite direction with the original transfer code in the reason. This preserves the original handover and correcting movement. There is no automatic reverse-transfer button or receiver-approval workflow in this version.

Inter-shop transfers support the shared business's branch stock, not sales between separate legal companies. Accessory-return UI, POS cash tender/change, final shop/hardware acceptance and cutover remain separate work.
