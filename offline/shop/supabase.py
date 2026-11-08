"""Server-side Supabase Auth and Storage access. Keys never reach templates."""
import json,time
from urllib.error import HTTPError,URLError
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen
from django.conf import settings
from django.core.exceptions import ValidationError

class ProviderUnavailable(Exception):pass

def call(path,method='GET',payload=None,token=None,admin=False,content_type='application/json',raw_response=False):
    key=settings.SUPABASE_SECRET_KEY if admin else settings.SUPABASE_PUBLISHABLE_KEY
    headers={'apikey':key,'Content-Type':content_type}
    if token:headers['Authorization']='Bearer '+token
    raw=payload if isinstance(payload,bytes) else json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(settings.SUPABASE_INTERNAL_URL+path,data=raw,headers=headers,method=method),timeout=10) as response:
            body=response.read()
            return json.loads(body) if not raw_response and 'json' in response.headers.get('Content-Type','') and body else body
    except HTTPError as error:
        # Provider responses can include user data or tokens. Do not echo response bodies.
        if error.code in (400,401,403,404,409,422):raise ValidationError('The authentication or file request was rejected. Check the account, permissions and supplied details.') from None
        raise ProviderUnavailable('The Supabase service could not complete the request.') from None
    except (URLError,TimeoutError,OSError):raise ProviderUnavailable('The Supabase service is unavailable.') from None

def sign_in(email,password):return call('/auth/v1/token?grant_type=password',method='POST',payload={'email':email,'password':password})
def refresh(token):return call('/auth/v1/token?grant_type=refresh_token',method='POST',payload={'refresh_token':token})
def user(token):return call('/auth/v1/user',token=token)
def sign_out(token):return call('/auth/v1/logout?scope=local',method='POST',token=token)
def create_user(email,password,metadata=None):return call('/auth/v1/admin/users',method='POST',payload={'email':email,'password':password,'email_confirm':True,'app_metadata':metadata or {}},admin=True)
def delete_user(uid):return call('/auth/v1/admin/users/'+quote(str(uid),safe=''),method='DELETE',admin=True)

def tokens(request,response):
    request.session['supabase']={key:response[key] for key in ('access_token','refresh_token')}
    request.session['supabase']['expires_at']=response.get('expires_at') or int(time.time())+int(response.get('expires_in',3600))

def object_path(name):return '/storage/v1/object/pta-evidence/'+quote(name,safe='/')
