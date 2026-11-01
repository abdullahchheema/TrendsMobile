import posixpath
import uuid
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.core.exceptions import ValidationError
from django.urls import reverse
from . import supabase

class SupabasePrivateStorage(Storage):
    def get_available_name(self,name,max_length=None):
        directory,filename=posixpath.split(name)
        prefix=uuid.uuid4().hex+'_'
        if max_length:
            budget=max_length-len(directory)-len(prefix)-1
            if budget<1:raise ValidationError('Attachment filename is too long.')
            filename=filename[:budget]
        return posixpath.join(directory,prefix+filename)
    def _save(self,name,content):
        raw=b''.join(content.chunks())
        if len(raw)>10485760:raise ValidationError('Attachment must not exceed 10 MB.')
        supabase.call(supabase.object_path(name),method='POST',payload=raw,admin=True,content_type=getattr(content,'content_type',None) or 'application/octet-stream')
        return name
    def _open(self,name,mode='rb'):
        if mode not in ('r','rb'):raise ValueError('Storage objects are read-only when opened.')
        body=supabase.call(supabase.object_path(name),admin=True,raw_response=True)
        if not isinstance(body,bytes):raise ValidationError('Unexpected attachment response.')
        return ContentFile(body,name=name)
    def exists(self,name):
        try:supabase.call(supabase.object_path(name),method='HEAD',admin=True);return True
        except ValidationError:return False
    def delete(self,name):supabase.call('/storage/v1/object/pta-evidence',method='DELETE',payload={'prefixes':[name]},admin=True)
    def url(self,name):return reverse('attachment',args=[name])
