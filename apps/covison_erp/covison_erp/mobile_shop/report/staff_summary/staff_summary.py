from covison_erp.staff_reports import summary

def execute(filters=None):
    columns,rows=summary(filters)
    return columns,rows,'Net phone purchases and sales include returns. Money collected / paid includes actual posted payments and POS receipts; exchange accounting offsets and internal transfers are excluded. Sales and collections must not be added together.'
