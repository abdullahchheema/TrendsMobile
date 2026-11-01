"""Generate local secrets once without overwriting an existing installation."""
from pathlib import Path
import secrets
root=Path(__file__).resolve().parent
path=root/'.env'
if not path.exists():
    path.write_text('DJANGO_SECRET_KEY='+secrets.token_urlsafe(64)+'\nPOSTGRES_PASSWORD='+secrets.token_urlsafe(40)+
        '\nDJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,web,testserver\nCOVISON_BIND_ADDRESS=127.0.0.1\n')
    path.chmod(0o600)
for folder in ('imports','backups','artifacts'):(root/folder).mkdir(exist_ok=True)
print('Local configuration ready; existing secrets preserved.')
