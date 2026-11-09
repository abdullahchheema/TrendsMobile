"""Remember the caller's IP address for the duration of a request so audit rows can record it."""
from contextvars import ContextVar
_ip=ContextVar('covison_client_ip',default=None)


def client_ip():return _ip.get()


class RequestContextMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        forwarded=request.META.get('HTTP_X_FORWARDED_FOR','').split(',')[0].strip()
        token=_ip.set((forwarded or request.META.get('REMOTE_ADDR') or None))
        try:return self.get_response(request)
        finally:_ip.reset(token)
