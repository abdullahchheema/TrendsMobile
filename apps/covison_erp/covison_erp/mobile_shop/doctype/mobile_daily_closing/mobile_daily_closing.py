import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate, now_datetime
from covison_erp.workflows import pkr_company


def balances(company, date):
    pkr_company(company)
    methods = frappe.get_list('Mode of Payment', filters={'enabled': 1}, pluck='name', limit_page_length=0)
    accounts = sorted(set(frappe.get_all('Mode of Payment Account', filters={'company': company,
        'parent': ['in', methods or ['']]}, pluck='default_account')) - {None, ''})
    result = []
    for account in accounts:
        doc = frappe.get_doc('Account', account); doc.check_permission('read')
        if doc.disabled or doc.is_group or doc.account_currency != 'PKR' or doc.account_type not in ('Cash', 'Bank'):
            continue
        opening, movement = frappe.db.sql('''select
            coalesce(sum(case when posting_date < %s then debit-credit else 0 end),0),
            coalesce(sum(case when posting_date = %s then debit-credit else 0 end),0)
            from `tabGL Entry` where company=%s and account=%s and is_cancelled=0 and posting_date<=%s''',
            (date, date, company, account, date))[0]
        result.append(dict(account=account, opening_balance=flt(opening, 2), movement=flt(movement, 2),
                           expected_balance=flt(opening+movement, 2)))
    return result


class MobileDailyClosing(Document):
    @frappe.whitelist()
    def load_balances(self):
        self.check_permission('write')
        previous = {r.account: r for r in self.balances}
        self.set('balances', [dict(r, counted_balance=previous[r['account']].counted_balance if r['account'] in previous else 0,
             notes=previous[r['account']].notes if r['account'] in previous else '') for r in balances(self.company, self.closing_date)])

    def validate(self):
        pkr_company(self.company)
        if getdate(self.closing_date) > getdate(nowdate()):
            frappe.throw('Cannot close a future date.')
        self.currency = 'PKR'
        expected = {r['account']: r for r in balances(self.company, self.closing_date)}
        if len(self.balances) != len(expected) or {r.account for r in self.balances} != set(expected):
            frappe.throw('Load all current payment account balances before saving.')
        for row in self.balances:
            row.update(expected[row.account])
            row.difference = flt(flt(row.counted_balance) - flt(row.expected_balance), 2)

    def before_submit(self):
        # Lock company to serialize simultaneous closings for the same company/date.
        frappe.db.sql('select name from `tabCompany` where name=%s for update', (self.company,))
        if frappe.db.exists('Mobile Daily Closing', {'company': self.company, 'closing_date': self.closing_date,
            'docstatus': 1, 'name': ['!=', self.name]}):
            frappe.throw('This company already has a submitted closing for this date.')
        if frappe.db.exists('POS Opening Entry', {'company': self.company, 'docstatus': 1, 'status': 'Open',
             'period_start_date': ['<', str(self.closing_date) + ' 23:59:59']}):
            frappe.throw('Close all open POS shifts through this date before daily closing.')
        self.validate()
        for row in self.balances:
            if row.difference and not (row.notes or '').strip():
                frappe.throw('Explain each cash / statement difference before submitting.')
        self.closed_by = frappe.session.user
        self.closed_at = now_datetime()
