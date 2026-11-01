import secrets
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
class Command(BaseCommand):
    help='Create the first local owner and write its password to a private local file.'
    def handle(self,*args,**kwargs):
        User=get_user_model()
        if User.objects.filter(is_superuser=True).exists():self.stdout.write('An owner already exists; credentials were preserved.');return
        password=secrets.token_urlsafe(24)
        User.objects.create_superuser(username='owner',email='',password=password)
        path=Path('/data/initial-owner.txt')
        path.write_text('Covison ERP local installation\nURL http://localhost:8081\nUsername owner\nPassword '+password+'\nChange this password after signing in.\n')
        path.chmod(0o600)
        self.stdout.write('Local owner created. Credentials are in /data/initial-owner.txt; no password was printed.')
