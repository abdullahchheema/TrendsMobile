from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from shop import books_check,online_check,tests_audit_invariants as invariants


class Command(BaseCommand):
    help=('The QA audit\'s system invariants (financial, inventory, online store, data ownership), plus the books check and the online order check. '
          'Reads only: it runs inside a read-only transaction and changes nothing. Exits with an error when anything is wrong.')

    def handle(self,*args,**options):
        bad=0;total=0
        with transaction.atomic():
            from django.db import connection
            with connection.cursor() as cursor:cursor.execute('SET TRANSACTION READ ONLY')        # belt and braces: the database itself refuses a write from here
            groups=[(check.__doc__.strip().split('\n')[0] if check.__doc__ else check.__name__,check) for check in invariants.INVARIANTS]
            groups+=[('Books check: '+name,check) for name,check in books_check.CHECKS]+[('Online orders agree with the stock',online_check.problems)]
            for name,check in groups:
                issues=check();total+=1;bad+=bool(issues)
                self.stdout.write(('OK   ' if not issues else 'FAIL ')+name+('' if not issues else f' ({len(issues)})'))
                for line in issues[:8]:self.stdout.write('       - '+line)
                if len(issues)>8:self.stdout.write(f'       ... and {len(issues)-8} more')
        self.stdout.write(f'\n{total-bad} of {total} checks passed.')
        if bad:raise CommandError(f'{bad} check(s) found problems.')
