from django.core.management.base import BaseCommand
from shop import seed_online


class Command(BaseCommand):
    help='Fill the demo stock with selling prices, website specifications and illustrated product photos so the online store is populated. Safe to repeat.'
    def add_arguments(self,parser):parser.add_argument('--dry-run',action='store_true',help='Only count what would be done.')
    def handle(self,*args,**options):
        self.stdout.write('Seeding the online store…')
        result=seed_online.run(write=self.stdout.write,dry=options['dry_run'])
        self.stdout.write(self.style.SUCCESS(f"{'Would set' if options['dry_run'] else 'Done'}: {result['prices']} selling prices, {result['specs']} specification sets, {result['photos']} photos, {result['live']} pages live."))
