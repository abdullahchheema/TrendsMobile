import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand,CommandError
from shop.models import Branch,Party
from shop import books_check,stress_data


class Command(BaseCommand):
    help=('Add a realistic 90-day business story (phones, IMEIs, sales, credit, returns, transfers, expenses, accounting) to the two shops. '
          'Additive only: nothing existing is changed or deleted. Test records are named "TEST · …" and noted "TEST DATA".')

    def add_arguments(self,parser):
        parser.add_argument('--days',type=int,default=90);parser.add_argument('--seed',type=int,default=20261110)
        parser.add_argument('--tag',default='TEST-2026-11');parser.add_argument('--again',action='store_true',help='allow a second run (adds more data)')
        parser.add_argument('--summary-file',default='');parser.add_argument('--no-verify',action='store_true')

    def handle(self,*args,**o):
        owner=get_user_model().objects.filter(is_superuser=True).order_by('date_joined').first()
        if not owner:raise CommandError('No owner (super admin) account found.')
        shops=list(Branch.objects.filter(enabled=True).order_by('code','name')[:2])
        if len(shops)<2:raise CommandError('Two shops are needed.')
        if Party.objects.filter(name__startswith=stress_data.DOT).exists() and not o['again']:raise CommandError('Test data is already present. Use --again to add more.')
        self.stdout.write(f"Owner {owner.get_username()} · shops: {', '.join(s.name for s in shops)} · {o['days']} days")
        story=stress_data.Story(owner,shops,tag=o['tag'],seed=o['seed'],days=o['days'],log=self.stdout.write);result=story.run()
        if o['summary_file']:open(o['summary_file'],'w',encoding='utf8').write(json.dumps(result,indent=1,default=str))
        for k,v in sorted(result['counts'].items()):self.stdout.write(f'  {k}: {v}')
        crashes=[f for f in result['failures'] if f[1].startswith('CRASH')]
        self.stdout.write(f"rejected/failed events: {sum(v for k,v in result['counts'].items() if k.startswith(('rejected','CRASH')))} (crashes: {len(crashes)})")
        for label,why in result['failures'][:25]:self.stdout.write(f'   · {label}: {why}')
        if not o['no_verify']:
            bad=0
            for name,issues in books_check.run_all():
                self.stdout.write(('OK   ' if not issues else 'FAIL ')+name+('' if not issues else f' ({len(issues)})'));bad+=bool(issues)
                for line in issues[:6]:self.stdout.write('       - '+line)
            self.stdout.write(f'{len(books_check.CHECKS)-bad} of {len(books_check.CHECKS)} consistency checks passed.')
