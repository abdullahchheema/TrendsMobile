import os
from urllib.parse import unquote
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from django.core.wsgi import get_wsgi_application
_django=get_wsgi_application()


def decode_path_info(environ):
    """Vercel's Python runtime can hand the WSGI app a still-percent-encoded PATH_INFO ("/records/Purchase%20Receipt/"),
    which then matches no URL. Decode it once there (latin-1 keeps the WSGI str/bytes convention intact)."""
    path=environ.get('PATH_INFO','')
    if os.environ.get('VERCEL') and '%' in path:environ['PATH_INFO']=unquote(path,encoding='latin-1')
    return environ


def application(environ,start_response):
    return _django(decode_path_info(environ),start_response)
