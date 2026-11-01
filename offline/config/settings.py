import os
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent.parent
SECRET_KEY=os.environ['DJANGO_SECRET_KEY']
DEBUG=False
ALLOWED_HOSTS=os.environ.get('DJANGO_ALLOWED_HOSTS','localhost,127.0.0.1').split(',')
INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','shop']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
    'shop.supabase_auth.SupabaseSessionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF='config.urls'
TEMPLATES=[dict(BACKEND='django.template.backends.django.DjangoTemplates',DIRS=[BASE_DIR/'templates'],APP_DIRS=True,
    OPTIONS=dict(context_processors=['django.template.context_processors.request','django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages','shop.access.navigation']))]
WSGI_APPLICATION='config.wsgi.application'
DATABASES={'default':dict(ENGINE='django.db.backends.postgresql',NAME=os.environ.get('DATABASE_NAME','covison'),
    USER=os.environ.get('DATABASE_USER','covison'),PASSWORD=os.environ['DATABASE_PASSWORD'],
    HOST=os.environ.get('DATABASE_HOST','db'),PORT=os.environ.get('DATABASE_PORT','5432'),CONN_MAX_AGE=60,
    OPTIONS={'options':'-c search_path='+os.environ.get('DATABASE_SCHEMA','public')+',public'},
    TEST={'NAME':os.environ.get('TEST_DATABASE_NAME','test_covison')})}
SUPABASE_INTERNAL_URL=os.environ.get('SUPABASE_INTERNAL_URL','').rstrip('/')
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
