"""Staff directory and responsibility reports; no payroll or invented employees."""
def build(APP, field, schema, write_json):
    import json
    schema('Mobile Staff', [
        field('staff_name', 'Staff Name', reqd=1, in_list_view=1),
        field('enabled', 'Active', 'Check', default=1, in_standard_filter=1),
        field('company', 'Company', 'Link', options='Company', reqd=1, in_standard_filter=1),
        field('warehouse', 'Shop / Warehouse', 'Link', options='Warehouse', in_standard_filter=1),
        field('designation', 'Role / Designation', 'Select', options='Manager\nSalesperson\nPurchaser\nCashier\nTechnician\nOther', default='Salesperson', in_list_view=1),
        field('user', 'Linked Login (Optional)', 'Link', options='User', description='A linked login selects this staff member automatically. Staff names do not grant system permissions.'),
        field('duties_section', 'Responsibilities', 'Section Break'),
        field('can_sell', 'Sells Devices', 'Check', default=1, in_list_view=1),
        field('can_buy', 'Buys Devices', 'Check', default=0, in_list_view=1),
        field('can_handle_payments', 'Collects / Pays Money', 'Check', default=0, in_list_view=1),
    ], autoname='COV-STF-.####', title_field='staff_name', search_fields='staff_name,designation,warehouse', allow_rename=0)
    path = APP / 'mobile_shop/doctype/mobile_staff/mobile_staff.json'
    data = json.loads(path.read_text())
    data['permissions'] = [dict(role=r,read=1,write=1,create=1,report=1,print=1) for r in ('System Manager','Mobile Shop Manager')]
    data['permissions'] += [dict(role=r,read=1,select=1) for r in ('Mobile Salesperson','Mobile Cashier','Mobile Purchaser','Mobile Technician','Sales User','Purchase User','Accounts User','Stock User')]
    write_json(path,data)
    (path.parent/'__init__.py').touch()
    for name in ('Staff Activity','Staff Summary'):
        slug=name.lower().replace(' ','_')
        folder=APP/'mobile_shop/report'/slug
        folder.mkdir(parents=True,exist_ok=True);(folder/'__init__.py').touch()
        write_json(folder/f'{slug}.json',dict(doctype='Report',name=name,report_name=name,ref_doctype='Mobile Staff',
            report_type='Script Report',is_standard='Yes',module='Mobile Shop',disabled=0,add_total_row=0,
            roles=[dict(role=r) for r in ('System Manager','Mobile Shop Manager')]))
