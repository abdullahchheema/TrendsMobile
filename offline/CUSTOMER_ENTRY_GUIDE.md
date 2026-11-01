# Enter customer names directly

In POS, sales invoices, payments, exchanges, warranty claims and customer trade-ins, type the customer name in the name box. Suggestions are optional; there is no need to create or select the customer first.

- A new name creates a customer when the draft is saved or the POS sale completes. You can add phone, address and other details later under Customers.
- An existing name reuses the same customer account. Capitalization and extra spaces are ignored when matching. Urdu names are supported.
- Changing the typed name while editing a draft changes that draft’s customer; it does not rename the old customer or move their earlier transactions.
- Clearing the required sales customer name blocks saving. A customer payment and supplier payment cannot be combined in the same payment entry.
- Return credits keep the original invoice customer fixed.

The Customer master retains its existing unique-name design. Give different people distinct saved names if they share a name. Ambiguous or inactive accounts are flagged for review instead of being silently merged or reactivated.

POS retries keep the same customer name and request key. A repeated request creates one sale and one customer. Failed POS checkout or rejected draft saving rolls back any customer created by that request. Simultaneous typing of the same new name at two counters resolves to one account.
