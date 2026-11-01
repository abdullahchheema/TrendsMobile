# Shop PC, scanner and printer acceptance

The Supabase preview has a **PC, scanner & printer check** link in its sidebar. Open `/device-check/` after signing in. These diagnostics do not create invoices, stock movements or payments. Actual shop names, staff assignments and payment methods are still awaiting the client's details; configure them through the owner setup before accepting a shop for trading.

## Shared-server connection

Each shop computer uses a browser to reach the shared server's private LAN address, currently `http://10.202.44.209:8081`. Use the installed server's actual address after deployment. Test each physical premises: the four shops must have a working network route to this server. Merely sharing the same business does not put separate premises on one LAN.

The connection check contacts the local app and database. It does not use the browser's internet indicator to decide whether trading is possible. Supabase sign-in and an approved test transaction must also succeed on each PC. Daily users do not need Python or Docker installed on their PCs.

Disconnect internet while leaving the LAN and server connected: local operation should continue. Disconnecting the LAN or stopping the shared server prevents new transactions. This version has no independent per-PC offline database or queued sales. Keep the server and local network powered during trading.

An optional public, read-only diagnostic uses Python 3's standard library:

```sh
python3 supabase/client_check.py --url http://SERVER_LAN_IP:8081 --output shop-pc-check.json
```

It checks the server/database health, login page and local assets. It records the client OS, Python version and timings, but no credentials or PC serial number. It cannot certify printers, scanners, staff permissions or connectivity at another shop.

## Scanner

Use a keyboard-style scanner configured to send the complete barcode followed by Enter. Select the scanner field on the check page, scan a real label and compare every digit, including leading zeros. The diagnostic distinguishes valid-format IMEIs from other barcode text; it does not verify PTA status or ownership.

In POS, scanning an exact IMEI or item barcode and pressing Enter adds the matching item. The same serialized phone cannot be added twice. If an item exists in multiple warehouses, choose the correct item and warehouse manually. Test both a phone and an accessory barcode used by the shop. In an IMEI intake field, Enter moves focus instead of submitting the draft unexpectedly.

Typing a code or simulating keyboard events verifies software handling only. A real scan must be checked on the actual scanner and PC.

## Printer

Install the printer's OS driver on each PC that will print. On the check page choose **58 mm**, **80 mm** or **A4** to match the installed printer. These sheets are marked **PRINTER TEST — NOT A SALE**, with a synthetic IMEI and PKR 12,345.50. They never post a sale.

Print at 100% scale with browser headers and footers disabled. Select the correct paper size in the driver and measure the printed 40 mm ruler. Check that prices, IMEI digits, warranty text and Urdu text are readable and fit the paper. Confirm margins, paper feed and any cutter using the physical output. Actual receipts also offer a 58 mm option with the unit rate displayed within each item line.

Browser-rendered screenshots establish layout only. Windows/Linux drivers, real paper width, Urdu fonts, thermal output, cutters and cash drawers have not been certified. The browser does not automatically operate a cash drawer. Printer/scanner models and PC operating systems were not specified by the client.

## POS reconnection

The **Complete sale** button requires an open shift and a successful local-server check. If the server disappears before submission, keep the cart open and reconnect. Carts are held in page memory; refreshing, closing the tab or losing PC power can discard an unsent cart.

If submission times out or the result is uncertain, the editor is frozen and **Retry same sale** retains the identical payload and request key. Keep this page open, restore the connection, and retry that sale. If the login expired, sign in in another tab before retrying. Review recent invoices before starting another sale; do not recreate a possibly posted sale with a fresh request. A definitive initial validation error unlocks the editor for correction.

Browser tests simulated a failed response, blocked submission during disconnection and checked identical retry data. Separately, an isolated HTTP test replayed a real posted POS request and verified that the same invoice was returned without reducing stock twice. A physical network interruption during a shop transaction still needs supervised acceptance using an approved test environment.

## Onsite record

Complete one copy of this table per premises. Run trading checks in a designated test environment or with explicitly approved test records; diagnostic sheets alone do not test payments or accounting.

| Check | Result / equipment / evidence |
| --- | --- |
| Shop name, address and date | Pending |
| PC OS, browser and version | Pending |
| Shared server reached from this premises | Pending |
| Assigned staff login, shop and permissions | Pending |
| Shop stock, till and payment methods | Pending |
| Scanner make/model, exact label digits and Enter suffix | Pending |
| Phone duplicate and accessory warehouse selection | Pending |
| Printer make/model, driver and paper width | Pending |
| Test sheet: 40 mm ruler, PKR, IMEI, Urdu and feed | Pending |
| Approved receipt: IMEI, warranty, price and totals | Pending |
| Internet off, LAN/server on: sign-in and approved trade | Pending |
| LAN interruption: submission blocked, cart retained | Pending |
| Supervised uncertain-result retry: one invoice/stock change | Pending |
| Checked by / accepted by | Pending |

This step prepares hardware acceptance. It does not switch the shops from Frappe or complete the outstanding feature-parity work. Backup scheduling, recovery/startup on the final server and final cutover remain separate work.
