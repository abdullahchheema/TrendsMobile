import os
from pathlib import Path
from . import deploy
BASE_DIR=Path(__file__).resolve().parent.parent
SECRET_KEY=os.environ['DJANGO_SECRET_KEY']
DEBUG=False
ALLOWED_HOSTS,_VERCEL_ORIGINS=deploy.hosts_and_origins(os.environ)
INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','shop']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
    'shop.requestctx.RequestContextMiddleware','shop.supabase_auth.SupabaseSessionMiddleware','shop.online_access.OnlineAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','shop.csrf_pages.NoStaleHtmlMiddleware','shop.turbo.TurboFormMiddleware']
CSRF_FAILURE_VIEW='shop.csrf_pages.csrf_failed'
ROOT_URLCONF='config.urls'
TEMPLATES=[dict(BACKEND='django.template.backends.django.DjangoTemplates',DIRS=[BASE_DIR/'templates'],APP_DIRS=True,
    OPTIONS=dict(context_processors=['django.template.context_processors.request','django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages','shop.access.navigation']))]
WSGI_APPLICATION='config.wsgi.application'
DATABASES={'default':deploy.database(os.environ)}
# SUPABASE_URL is the hosted project URL (https://<ref>.supabase.co); SUPABASE_INTERNAL_URL is the self-hosted container address.
SUPABASE_INTERNAL_URL=(os.environ.get('SUPABASE_URL') or os.environ.get('SUPABASE_INTERNAL_URL','')).rstrip('/')
SUPABASE_AUTH_ENABLED=bool(SUPABASE_INTERNAL_URL)
SUPABASE_PUBLISHABLE_KEY=os.environ.get('SUPABASE_PUBLISHABLE_KEY','')
SUPABASE_SECRET_KEY=os.environ.get('SUPABASE_SECRET_KEY','')
AUTHENTICATION_BACKENDS=['shop.supabase_auth.SupabaseBackend'] if SUPABASE_AUTH_ENABLED else ['django.contrib.auth.backends.ModelBackend']
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE='en-gb'
TIME_ZONE='Asia/Karachi'
USE_I18N=True
USE_TZ=True
STATIC_URL='/static/'
STATIC_ROOT=BASE_DIR/'staticfiles'
STATICFILES_DIRS=[BASE_DIR/'static']
STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
if SUPABASE_AUTH_ENABLED:STORAGES['default']={'BACKEND':'shop.supabase_storage.SupabasePrivateStorage'}
ONLINE_API_KEY=os.environ.get('ONLINE_API_KEY','')      # the website's secret for POST /api/online/orders/ ; empty = the order API is switched off
WEBSITE_REVALIDATE_URL=os.environ.get('WEBSITE_REVALIDATE_URL','').strip()      # e.g. https://your-site.vercel.app/api/revalidate ; empty = the website just refreshes by its own cache
MEDIA_ROOT='/data/uploads'
MEDIA_URL='/media/'
LOGIN_URL='/login/'
LOGIN_REDIRECT_URL='/desk/mobile-shop'
LOGOUT_REDIRECT_URL='/login/'
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE='Lax'
CSRF_COOKIE_SAMESITE='Lax'
SECURE_CONTENT_TYPE_NOSNIFF=True
X_FRAME_OPTIONS='DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE=10485760
FILE_UPLOAD_MAX_MEMORY_SIZE=5242880
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'

# Hardening. The shop LAN runs plain HTTP by default; set COVISON_HTTPS=1 once TLS terminates in front of the app
# so cookies become Secure and browsers are told to stay on HTTPS.
SESSION_COOKIE_AGE=int(os.environ.get('COVISON_SESSION_HOURS','12'))*3600
SESSION_SAVE_EVERY_REQUEST=False
CSRF_TRUSTED_ORIGINS=_VERCEL_ORIGINS
if os.environ.get('VERCEL'):
    # Vercel always serves over HTTPS and sets this header itself, so Django must see the request as secure; otherwise its
    # same-origin check compares the browser's https:// origin with http:// and rejects valid form posts.
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
if os.environ.get('COVISON_HTTPS')=='1':
    SESSION_COOKIE_SECURE=CSRF_COOKIE_SECURE=SECURE_SSL_REDIRECT=True
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
    SECURE_HSTS_SECONDS=31536000
    SECURE_REFERRER_POLICY='same-origin'
LOGIN_MAX_FAILURES=int(os.environ.get('COVISON_LOGIN_MAX_FAILURES','8'))
LOGIN_LOCKOUT_SECONDS=15*60

if deploy.static_from_finders(os.environ):
    STORAGES['staticfiles']={'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}
    WHITENOISE_USE_FINDERS=True
if deploy.uses_database_cache(os.environ):
    CACHES={'default':{'BACKEND':'django.core.cache.backends.db.DatabaseCache','LOCATION':'covison_cache'}}
