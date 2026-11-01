import time
from django.conf import settings
from django.contrib.auth import get_user_model,logout
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from .models import SupabaseIdentity
from . import supabase

class SupabaseBackend:
    def authenticate(self,request,username=None,password=None,**kwargs):
        if not request or not username or not password:return None
        try:
            result=supabase.sign_in(username,password)
            identity=SupabaseIdentity.objects.select_related('user').get(auth_id=result['user']['id'],user__is_active=True)
            if not identity.user.is_superuser:
                person=getattr(identity.user,'staff',None)
                if not person or not person.enabled or not person.branch.enabled:
                    supabase.sign_out(result['access_token']);return None
            supabase.tokens(request,result)
            return identity.user
        except (ValidationError,supabase.ProviderUnavailable,SupabaseIdentity.DoesNotExist,KeyError):return None
    def get_user(self,user_id):
        return get_user_model().objects.filter(pk=user_id,is_active=True,supabase_identity__isnull=False).first()

class SupabaseSessionMiddleware:
    """Keep provider sessions server-side and fail closed if identity is revoked."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        # Public liveness polling must not refresh or revoke an idle staff session.
        if request.path=='/health/':return self.get_response(request)
        if settings.SUPABASE_AUTH_ENABLED and request.user.is_authenticated:
            current=request.session.get('supabase')
            try:
                if not current:raise ValidationError('Provider session is required.')
                if int(current['expires_at'])<int(time.time())+45:
                    supabase.tokens(request,supabase.refresh(current['refresh_token']));current=request.session['supabase']
                actual=supabase.user(current['access_token'])
                if str(request.user.supabase_identity.auth_id)!=actual['id']:raise ValidationError('Identity does not match.')
            except (ValidationError,supabase.ProviderUnavailable,SupabaseIdentity.DoesNotExist,KeyError,ValueError):
                logout(request)
                return redirect(settings.LOGIN_URL)
        return self.get_response(request)
