from django.core.management.base import BaseCommand,CommandError
from shop import online_check


class Command(BaseCommand):
    help='Check that online orders and physical stock agree (read-only). Exits with an error when something is wrong.'
    def handle(self,*args,**options):
        found=online_check.problems()
        for line in found:self.stdout.write('PROBLEM: '+line)
        if found:raise CommandError(f'{len(found)} problem(s) found.')
        self.stdout.write('Online orders and stock agree.')
