"""Slow down password guessing: after LOGIN_MAX_FAILURES wrong attempts for the same username+address the login is
locked for LOGIN_LOCKOUT_SECONDS. Counters live in Django's cache (per worker unless a shared cache is configured),
so the real limit is at most workers x LOGIN_MAX_FAILURES; a success clears the counter."""
from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse


def _key(request):
    name=(request.POST.get('username') or '').strip().lower()[:150]
    return 'login-fail:%s:%s'%(request.META.get('REMOTE_ADDR',''),name)


class ThrottledLoginView(LoginView):
    template_name='login.html'

    def post(self,request,*args,**kwargs):
        if cache.get(_key(request),0)>=settings.LOGIN_MAX_FAILURES:
            return HttpResponse('Too many failed sign-in attempts. Wait 15 minutes and try again.',status=429,content_type='text/plain')
        return super().post(request,*args,**kwargs)

    def form_valid(self,form):
        cache.delete(_key(self.request));return super().form_valid(form)

    def form_invalid(self,form):
        key=_key(self.request);cache.add(key,0,settings.LOGIN_LOCKOUT_SECONDS)
        try:cache.incr(key)
        except ValueError:cache.set(key,1,settings.LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)
