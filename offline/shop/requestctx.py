"""Remember the caller's IP address for the duration of a request so audit rows can record it, and turn away a request that carries
a NUL character (the database cannot store or even search for one, so every page that used the text answered with a server error)."""
from contextvars import ContextVar
from django.http import HttpResponseBadRequest,JsonResponse
from . import reqcache
_ip=ContextVar('covison_client_ip',default=None)


def client_ip():return _ip.get()


NUL_MESSAGE='This request contains a character that cannot be saved or searched for (NUL). Remove it and try again.'


def has_nul(request):
    """True when the address, a form field or a JSON body carries a NUL. File uploads are not read here (photos are checked by their own bytes)."""
    query=request.META.get('QUERY_STRING','')
    if '%00' in query or '\x00' in query:return True
    kind=(request.content_type or '').lower()
    if request.method in ('POST','PUT','PATCH') and (kind.startswith('application/x-www-form-urlencoded') or 'json' in kind):
        try:body=request.body
        except Exception:return False                 # too large or unreadable: the view's own limits answer that
        return b'\x00' in body or (b'\\u0000' in body if 'json' in kind else b'%00' in body)
    return False


class RequestContextMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if has_nul(request):
            return JsonResponse({'error':NUL_MESSAGE},status=400) if 'json' in (request.content_type or '').lower() else HttpResponseBadRequest(NUL_MESSAGE,content_type='text/plain; charset=utf-8')
        forwarded=request.META.get('HTTP_X_FORWARDED_FOR','').split(',')[0].strip()
        token=_ip.set((forwarded or request.META.get('REMOTE_ADDR') or None))
        memo=reqcache.start() if request.method in ('GET','HEAD') else None
        try:return self.get_response(request)
        finally:
            _ip.reset(token)
            if memo is not None:reqcache.stop(memo)
