"""A stale form (an old tab, a restored phone browser tab, a login page left open before the session changed) used to end on a
bare "403 Forbidden - CSRF verification failed" page. The security check is unchanged; only what the person sees is."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def csrf_failed(request,reason=''):
    if 'json' in (request.content_type or '') or 'json' in request.META.get('HTTP_ACCEPT','') or request.headers.get('x-requested-with'):
        return JsonResponse({'error':'This page had been open too long. Reload the page and try again. Nothing was saved.'},status=403)
    messages.error(request,'That page had been open too long and could not be saved. Nothing was changed. Please try again.')
    back=request.META.get('HTTP_REFERER','')
    if request.user.is_authenticated:
        if back and url_has_allowed_host_and_scheme(back,{request.get_host()},require_https=request.is_secure()):return redirect(back)
        return redirect('/')
    return redirect(reverse('login'))


class NoStaleHtmlMiddleware:
    """Pages carry per-session security tokens and live figures, so browsers must not replay them from their back/restore cache."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        if 'Cache-Control' not in response and response.get('Content-Type','').startswith('text/html'):response['Cache-Control']='private, no-store'
        return response
