import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from shop.models import SupabaseIdentity
from shop import supabase
class Command(BaseCommand):
    help='Create the first explicitly linked Supabase owner without resetting existing accounts.'
    @transaction.atomic
    def handle(self,*args,**kwargs):
        if not settings.SUPABASE_AUTH_ENABLED:raise CommandError('Supabase Auth is not configured.')
        if SupabaseIdentity.objects.filter(user__is_superuser=True).exists():self.stdout.write('A linked owner exists; account and password preserved.');return
        email=os.environ['COVISON_CRM_OWNER_EMAIL'];password=os.environ['COVISON_CRM_OWNER_PASSWORD']
        found=None
        for page in range(1,101):
            rows=supabase.call(f'/auth/v1/admin/users?page={page}&per_page=1000',admin=True).get('users',[])
            found=next((row for row in rows if row.get('email','').lower()==email.lower()),None)
            if found or len(rows)<1000:break
        if found and not found.get('app_metadata',{}).get('covison_initial_owner'):raise CommandError('The configured owner email already belongs to an unlinked account; review it before granting access.')
        if not found:found=supabase.create_user(email,password,metadata={'covison_initial_owner':True})
        User=get_user_model()
        user,created=User.objects.get_or_create(username=email,defaults={'email':email,'is_staff':True,'is_superuser':True})
        if not created and not user.is_superuser:raise CommandError('Existing local account is not an owner; no privileges were changed.')
        user.set_unusable_password();user.save()
        SupabaseIdentity.objects.create(auth_id=found['id'],user=user)
        self.stdout.write('Supabase owner linked. Initial credentials are in supabase/private/SERVER-LOGIN.txt.')
