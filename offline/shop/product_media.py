"""Where product photos are stored. On the live system they go to a PUBLIC Supabase Storage bucket (the website needs a plain
URL); on a developer machine they go to a local folder. Only images are accepted, checked by their real bytes, not by name."""
import posixpath
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from . import supabase

BUCKET='product-images'
MAX_BYTES=5*1024*1024
MAX_PER_PRODUCT=12
TYPES={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}


def sniff(raw):
    """The real type of an image from its first bytes (JPEG, PNG or WebP), or '' when it is anything else."""
    if raw[:3]==b'\xff\xd8\xff':return 'image/jpeg'
    if raw[:8]==b'\x89PNG\r\n\x1a\n':return 'image/png'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':return 'image/webp'
    return ''


def check(raw):
    if not raw:raise ValidationError('The file is empty.')
    if len(raw)>MAX_BYTES:raise ValidationError(f'This photo is {len(raw)/1048576:.1f} MB. Photos can be at most {MAX_BYTES//1048576} MB.')
    kind=sniff(raw)
    if not kind:raise ValidationError('Only JPG, PNG or WebP photos can be added.')
    return kind


def _local_dir():
    import pathlib
    return pathlib.Path(getattr(settings,'PRODUCT_IMAGE_DIR',None) or settings.BASE_DIR/'product_images_local')


def public_base():
    return (getattr(settings,'SUPABASE_PUBLIC_URL','') or settings.SUPABASE_INTERNAL_URL).rstrip('/')


def _ensure_bucket():
    try:supabase.call('/storage/v1/bucket',method='POST',payload={'id':BUCKET,'name':BUCKET,'public':True,'file_size_limit':MAX_BYTES,'allowed_mime_types':list(TYPES)},admin=True)
    except ValidationError:pass                  # already exists


def save(product_id,raw):
    """Store one photo. Returns (storage path, public URL, size)."""
    kind=check(raw);path=posixpath.join('products',str(product_id),uuid.uuid4().hex+'.'+TYPES[kind])
    if settings.SUPABASE_AUTH_ENABLED:
        _ensure_bucket()
        supabase.call(f'/storage/v1/object/{BUCKET}/{path}',method='POST',payload=raw,admin=True,content_type=kind)
        return path,f'{public_base()}/storage/v1/object/public/{BUCKET}/{path}',len(raw)
    target=_local_dir()/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    return path,reverse('product_image_file',args=[path]),len(raw)


def delete(path):
    if settings.SUPABASE_AUTH_ENABLED:
        try:supabase.call(f'/storage/v1/object/{BUCKET}',method='DELETE',payload={'prefixes':[path]},admin=True)
        except (ValidationError,supabase.ProviderUnavailable):pass       # the record is removed either way; a stray file harms nothing
        return
    try:(_local_dir()/path).unlink()
    except OSError:pass


def read_local(path):
    target=(_local_dir()/path).resolve()
    if _local_dir().resolve() not in target.parents or not target.is_file():return None
    return target.read_bytes()
