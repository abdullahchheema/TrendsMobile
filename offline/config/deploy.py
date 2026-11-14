"""Environment-driven deployment settings (kept separate from settings.py so they can be unit-tested).

Two supported database set-ups:
* DATABASE_URL (hosted Supabase / any pooled Postgres, used on Vercel): one connection string. Connections are not kept
  between requests and prepared statements are off so Supabase's transaction pooler (port 6543) works. The Django schema
  comes from the database role's own search_path (see supabase/hosted/setup.sql), because poolers reject startup options.
* DATABASE_NAME / _USER / _PASSWORD / _HOST / _PORT / _SCHEMA (the bundled self-hosted Docker stack).
"""
from urllib.parse import unquote,urlparse


def database(environ):
    url=environ.get('DATABASE_URL','').strip()
    test={'NAME':environ.get('TEST_DATABASE_NAME','test_covison')}
    if url:
        parts=urlparse(url)
        if parts.scheme not in ('postgres','postgresql') or not parts.hostname or not parts.username:
            raise ValueError('DATABASE_URL must look like postgresql://user:password@host:port/database')
        port=parts.port or 5432
        # The transaction pooler (6543) is built for long-lived clients, so a warm serverless instance keeps its connection and
        # skips the TLS handshake + login on every page. Other poolers stay one-connection-per-request unless asked.
        keep=environ.get('DATABASE_CONN_MAX_AGE','').strip()
        keep=int(keep) if keep.isdigit() else (300 if port==6543 else 0)
        return dict(ENGINE='django.db.backends.postgresql',NAME=unquote(parts.path.lstrip('/')) or 'postgres',USER=unquote(parts.username),
            PASSWORD=unquote(parts.password or ''),HOST=parts.hostname,PORT=str(port),CONN_MAX_AGE=keep,CONN_HEALTH_CHECKS=bool(keep),DISABLE_SERVER_SIDE_CURSORS=True,
            OPTIONS={'sslmode':environ.get('DATABASE_SSLMODE','require'),'prepare_threshold':None},TEST=test)
    return dict(ENGINE='django.db.backends.postgresql',NAME=environ.get('DATABASE_NAME','covison'),USER=environ.get('DATABASE_USER','covison'),
        PASSWORD=environ['DATABASE_PASSWORD'],HOST=environ.get('DATABASE_HOST','db'),PORT=environ.get('DATABASE_PORT','5432'),CONN_MAX_AGE=60,
        OPTIONS={'options':'-c search_path='+environ.get('DATABASE_SCHEMA','public')+',public'},TEST=test)


def hosts_and_origins(environ):
    """Allowed hosts and CSRF origins: explicit settings plus the hostnames Vercel injects for the deployment."""
    hosts=[h.strip() for h in environ.get('DJANGO_ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if h.strip()]
    origins=[o.strip() for o in environ.get('COVISON_TRUSTED_ORIGINS','').split(',') if o.strip()]
    for name in ('VERCEL_URL','VERCEL_BRANCH_URL','VERCEL_PROJECT_PRODUCTION_URL'):
        host=environ.get(name,'').strip()
        if host and host not in hosts:hosts.append(host)
        if host and 'https://'+host not in origins:origins.append('https://'+host)
    return hosts,origins


def uses_database_cache(environ):
    """Login throttling needs a cache shared by every serverless instance, so use the database when pooled."""
    return bool(environ.get('DATABASE_URL')) or environ.get('COVISON_CACHE')=='db'


def secure_cookies(environ):
    """Sign-in and form cookies are sent over HTTPS only. Vercel never serves plain HTTP, so there it does not depend on anyone remembering
    to set COVISON_HTTPS (the live site was found without it: its cookies were not marked Secure)."""
    return bool(environ.get('VERCEL')) or environ.get('COVISON_HTTPS')=='1'


def static_from_finders(environ):
    """Vercel has no build step for the Python runtime, so WhiteNoise serves straight from the static/ folder."""
    return bool(environ.get('VERCEL')) or environ.get('COVISON_STATIC_FINDERS')=='1'
