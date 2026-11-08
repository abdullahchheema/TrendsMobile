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
    """Keep provider sessions server-side and fail closed if identity is revoked.

    The provider is asked who the session belongs to at most once per VERIFY_SECONDS (revocation is still noticed within
    that window). If the provider is merely unreachable, a session verified in the last OUTAGE_GRACE_SECONDS keeps working
    so a short Supabase blip does not sign every counter out; an explicit rejection always signs the user out."""
    VERIFY_SECONDS=30
    OUTAGE_GRACE_SECONDS=600
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        # Public liveness polling must not refresh or revoke an idle staff session.
        if request.path=='/health/':return self.get_response(request)
        if settings.SUPABASE_AUTH_ENABLED and request.user.is_authenticated:
            now=int(time.time());verified=int(request.session.get('supabase_verified_at',0))
            current=request.session.get('supabase')
            try:
                if not current:raise ValidationError('Provider session is required.')
                if now-verified>=self.VERIFY_SECONDS:
                    if int(current['expires_at'])<now+45:
                        supabase.tokens(request,supabase.refresh(current['refresh_token']));current=request.session['supabase']
                    actual=supabase.user(current['access_token'])
                    if str(request.user.supabase_identity.auth_id)!=actual['id']:raise ValidationError('Identity does not match.')
                    request.session['supabase_verified_at']=now
            except supabase.ProviderUnavailable:
                if not verified or now-verified>self.OUTAGE_GRACE_SECONDS:
                    logout(request);return redirect(settings.LOGIN_URL)
            except (ValidationError,SupabaseIdentity.DoesNotExist,KeyError,ValueError):
                logout(request)
                return redirect(settings.LOGIN_URL)
        return self.get_response(request)
