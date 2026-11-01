from covison_erp.staff_reports import activity,detail_columns

def execute(filters=None):
    return detail_columns(),activity(filters),'Posted transactions visible to your login. Purchase values come from phone receipts; bills are not counted again. Sales values and money collected are separate activities. Unassigned historical records remain identifiable. Offsets are not cash collections.'
