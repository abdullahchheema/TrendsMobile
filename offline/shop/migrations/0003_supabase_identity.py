from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion
class Migration(migrations.Migration):
    dependencies=[('shop','0002_device_branch'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(name='SupabaseIdentity',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('auth_id',models.UUIDField(unique=True)),
        ('user',models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,related_name='supabase_identity',to=settings.AUTH_USER_MODEL))])]
