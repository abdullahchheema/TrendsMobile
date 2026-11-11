from django.core.management.base import BaseCommand
from shop import books_check


class Command(BaseCommand):
    help='Audit the whole ERP: inventory, ledger, balances, trial balance and reports must all agree. Reads only; changes nothing.'

    def handle(self,*args,**options):
        bad=0
        for name,issues in books_check.run_all():
            mark='OK  ' if not issues else 'FAIL'
            self.stdout.write(f'{mark} {name}'+('' if not issues else f' ({len(issues)})'))
            for line in issues[:8]:self.stdout.write(f'       - {line}')
            if len(issues)>8:self.stdout.write(f'       … and {len(issues)-8} more')
            bad+=bool(issues)
        self.stdout.write(f'\n{len(books_check.CHECKS)-bad} of {len(books_check.CHECKS)} checks passed.')
