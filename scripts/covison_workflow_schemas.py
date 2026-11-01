"""Shop workflows kept separate from the core device schema."""

def build(APP, field, schema, write_json):
    def permissions(roles, submit=False):
        return [dict(role=r, read=1, write=1, create=1, print=1, report=1,
                     submit=int(submit), cancel=int(submit and r in ('System Manager', 'Mobile Shop Manager')))
                for r in roles]
    managers = ('System Manager', 'Mobile Shop Manager')
    buy = permissions(managers + ('Mobile Purchaser',), True)
    cash = permissions(managers + ('Mobile Cashier', 'Accounts Manager'), True)
    fields = [
        field('seller_tab', '1. Seller & Purchase', 'Tab Break'),
        field('company', 'Company', 'Link', options='Company', reqd=1),
        field('supplier', 'Seller / Supplier', 'Link', options='Supplier', reqd=1),
        field('purchase_source', 'Purchase Source', 'Select', options='Distributor\nDealer\nIndividual Seller\nCustomer Trade-In', default='Individual Seller', reqd=1),
        field('trade_in_customer', 'Exchange Customer', 'Link', options='Customer', depends_on="eval:doc.purchase_source=='Customer Trade-In'", mandatory_depends_on="eval:doc.purchase_source=='Customer Trade-In'"),
        field('seller_phone', 'Seller Phone', description='Use the seller contact number; do not collect identity documents unless your shop requires them.'),
        field('posting_date', 'Purchase Date', 'Date', default='Today', reqd=1),
        field('warehouse', 'Receiving Shop / Warehouse', 'Link', options='Warehouse', reqd=1),
        field('phone_section', '2. Phone & Inspection', 'Tab Break'),
        field('item_code', 'Model / Product Variant', 'Link', options='Item', reqd=1),
        field('currency', 'Currency', 'Link', options='Currency', default='PKR', read_only=1),
        field('purchase_price', 'Agreed Purchase Price (PKR)', 'Currency', options='currency', reqd=1),
    ]
    # Use identical inspection field names to copy explicitly into the device profile.
    import json
    device_path = APP / 'mobile_shop/doctype/mobile_device/mobile_device.json'
    device = json.loads(device_path.read_text())
    names = ['imei_1', 'imei_2', 'manufacturer_serial', 'regional_version', 'market_description',
             'device_type', 'condition_grade', 'battery_health_checked', 'battery_health',
             'inspection_status', 'inspection_date', 'display_condition', 'biometrics', 'camera_test',
             'charging_test', 'audio_test', 'network_test', 'carrier_lock', 'activation_lock',
             'sim_configuration', 'repair_history', 'included_accessories', 'inspection_notes',
             'pta_status', 'pta_verified_on', 'pta_verified_by', 'pta_evidence', 'pta_notes',
             'pta_status_2', 'pta_verified_on_2', 'pta_evidence_2', 'asking_price', 'minimum_price',
             'wholesale_price', 'warranty_days', 'warranty_terms', 'disclosed_faults']
    for f in device['fields']:
        if f['fieldname'] in names:
            if f['fieldname'] == 'pta_status':
                fields.append(field('verification_tab', '3. PTA Verification', 'Tab Break'))
            if f['fieldname'] == 'asking_price':
                fields.append(field('pricing_tab', '4. Selling Prices & Warranty', 'Tab Break'))
            f = dict(f)
            for k in ('unique', 'permlevel', 'in_list_view', 'read_only_depends_on', 'in_standard_filter'):
                f.pop(k, None)
            fields.append(f)
    fields += [field('links_section', 'Posted Records', 'Section Break'),
               field('purchase_receipt', 'Purchase Receipt', 'Link', options='Purchase Receipt', read_only=1, no_copy=1),
               field('mobile_device', 'Phone in Stock', 'Link', options='Mobile Device', read_only=1, no_copy=1),
               field('amended_from', 'Amended From', 'Link', options='Mobile Intake', read_only=1, no_copy=1)]
    schema('Mobile Intake', fields, autoname='COV-BUY-.#####', is_submittable=1)
    path = APP / 'mobile_shop/doctype/mobile_intake/mobile_intake.json'
    data = json.loads(path.read_text()); data['permissions'] = buy; write_json(path, data)

    schema('Mobile Closing Balance', [
        field('account', 'Cash / Bank / Wallet Account', 'Link', options='Account', reqd=1, in_list_view=1, read_only=1),
        field('opening_balance', 'Opening Ledger Balance', 'Currency', in_list_view=1, read_only=1),
        field('movement', 'Net Movement', 'Currency', read_only=1),
        field('expected_balance', 'Expected Closing Balance', 'Currency', in_list_view=1, read_only=1),
        field('counted_balance', 'Actual Cash / Statement Balance', 'Currency', in_list_view=1, reqd=1),
        field('difference', 'Difference', 'Currency', in_list_view=1, read_only=1),
        field('notes', 'Difference Explanation', 'Small Text'),
    ], istable=1)
    path = APP / 'mobile_shop/doctype/mobile_closing_balance/mobile_closing_balance.json'
    data = json.loads(path.read_text()); data['permissions'] = []; write_json(path, data)
    schema('Mobile Daily Closing', [
        field('company', 'Company', 'Link', options='Company', reqd=1),
        field('closing_date', 'Closing Date', 'Date', default='Today', reqd=1),
        field('currency', 'Currency', 'Link', options='Currency', default='PKR', read_only=1),
        field('scope_note', 'Scope', 'HTML', options='<p>Reconcile company cash, bank and wallet accounts. Shared payment accounts appear once. Close POS shifts first. Submitting records the comparison; it does not post an adjustment. Later backdated postings can change the ledger.</p>'),
        field('balances', 'Cash & Account Balances', 'Table', options='Mobile Closing Balance', reqd=1),
        field('notes', 'Closing Notes', 'Small Text'),
        field('closed_by', 'Closed By', 'Link', options='User', read_only=1),
        field('closed_at', 'Closed At', 'Datetime', read_only=1),
        field('amended_from', 'Amended From', 'Link', options='Mobile Daily Closing', read_only=1, no_copy=1),
    ], is_submittable=1, autoname='COV-CLOSE-.#####')
    path = APP / 'mobile_shop/doctype/mobile_daily_closing/mobile_daily_closing.json'
    data = json.loads(path.read_text()); data['permissions'] = cash; write_json(path, data)
    schema('Mobile Shop Settings', [
        field('shop_phone', 'Receipt Contact Number'), field('shop_address', 'Receipt Address', 'Small Text'),
        field('receipt_terms', 'Default Receipt / Checking Warranty Terms', 'Small Text'),
        field('urdu_receipt_note', 'Optional Urdu Receipt Note', 'Small Text'),
        field('ageing_days', 'Slow Stock Threshold (Days)', 'Int', default=30, reqd=1),
    ], issingle=1)
    path = APP / 'mobile_shop/doctype/mobile_shop_settings/mobile_shop_settings.json'
    data = json.loads(path.read_text()); data['permissions'] = permissions(managers); write_json(path, data)
    # Exchange mutation is a finance operation; read-only staff may inspect records.
    path = APP / 'mobile_shop/doctype/mobile_exchange/mobile_exchange.json'
    data = json.loads(path.read_text()); data['permissions'] = permissions(managers + ('Mobile Cashier', 'Accounts Manager'))
    for f in data['fields']:
        if f['fieldname'] in ('purchase_receipt', 'sales_invoice'):
            f['unique'] = 1
        if f['fieldname'] == 'notes':
            f['description'] = 'Settle offsets the posted customer and supplier invoices, then records the actual difference paid. Review both invoices first.'
    extra = [field('purchase_invoice', 'Incoming Purchase Invoice', 'Link', options='Purchase Invoice'),
             field('journal_entry', 'Posted Exchange Offset', 'Link', options='Journal Entry', read_only=1),
             field('payment_entry', 'Posted Difference Payment', 'Link', options='Payment Entry', read_only=1),
             field('settlement_status', 'Settlement Status', 'Select', options='Not Settled\nSettled\nReversed / Review', default='Not Settled', read_only=1)]
    data['fields'] += extra; data['field_order'] += [f['fieldname'] for f in extra]; write_json(path, data)
    for name in ('Mobile Intake', 'Mobile Closing Balance', 'Mobile Daily Closing', 'Mobile Shop Settings'):
        folder = APP / 'mobile_shop/doctype' / name.lower().replace(' ', '_')
        (folder / '__init__.py').touch()
