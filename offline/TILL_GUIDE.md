# Cash tills and POS shift closing

This workflow is available in the separate Supabase + Django preview. The original Frappe CRM has not been switched over.

## Manager setup

Open **Cash tills** in the sidebar. Existing shops receive a **Main till** when the schema upgrade runs. Managers can create additional tills for separate counters, choosing the correct shop. A new shop needs its tills configured before starting POS shifts.

Assign each cashier a staff login with payment permission. Only one open shift is allowed per till, cashier and login. A till with history cannot move to another shop; an open till cannot be disabled.

## Start and use a shift

1. Open **POS → Start shift**.
2. Select the till and cashier. Count the cash physically placed in the drawer and enter it as **Counted opening cash (PKR)**, including zero when empty.
3. Choose **Save draft**, review the details and press **Submit**.
4. Sell through POS. Keep the **Payment collected by** staff member equal to the shift cashier; the seller can be a different staff member.

Opening cash records who holds existing money. It does not create another accounting receipt or change the company's cash ledger.

Payments recorded during your open shift are linked to that till. This includes invoice collections, customer advances, supplier payments and refunds. Cash receipts increase its expected cash; cash payouts decrease it. Bank/card payments are recorded in the shift but excluded from physical cash. Classification follows the payment method's configured Cash or Bank account.

POS now records [cash handed and change](POS_CASH_GUIDE.md) separately. A PKR 5,000 note with PKR 500 change increases expected cash by PKR 4,500. Payment details retain both amounts; the till uses only net applied cash. Bank/card rows cannot generate cash change.

Payments made without an open shift remain shop payments and are excluded from till counts. Advance allocation does not collect money again. Cancelling an eligible payment while the shift is open removes its effect from the expected count.

## Count and close

1. In POS choose **End shift**, or open your shift under **Open / Past Shifts** and choose **Count cash & close shift**.
2. Physically count the drawer and enter **Physically counted closing cash (PKR)**.
3. Explain any shortage or excess in **Cash difference / legacy review explanation**.
4. Save the draft, review its till summary, then press **Submit**.

Expected cash is **opening cash + cash received − cash paid/refunded**. It is recalculated at submission, so a payment posted after the draft was saved is included. A difference requires an explanation; closing never generates an automatic accounting adjustment.

The submitted closing preserves the cashier, till, opening cash, receipts, payouts, expected cash, actual count, difference, explanation and included payments. Find it under **Till Closings**. Ordinary cancellation of a payment from a closed shift is blocked; a manager must review corrections.

Close all shifts before submitting **Daily Closing**. Daily Closing continues to reconcile the shop's Cash/Bank ledger balances; till custody and company account balances are separate measures.

## Imported legacy shifts

Older imported shifts do not have reliable links from every payment to a physical drawer. They are labelled **Legacy shift**. A manager must record the physical count and explain the review. Expected cash and difference remain **Unknown**, even when an old source contains an opening amount, because its complete till movements cannot be established.

A legacy review can use today's review date. New payments cannot be attached to a legacy shift. Review and close it, then start a new till shift with an actual opening count. Imported historical documents and their financial postings remain preserved.

## Deployment checks still pending

Physical printing, visual browser interaction, shop-computer access, Windows startup/recovery and HTTPS need validation on the intended hardware. The isolated workflow and concurrency tests do not establish completion of those checks.
