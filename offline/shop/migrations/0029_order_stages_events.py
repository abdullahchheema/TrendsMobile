"""Online order stages, dispatch notes, the return note and the permanent event log (who did what, when)."""
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('shop', '0028_web_views_v4')]
    operations = [
        migrations.AddField('onlineorder', 'stage', models.CharField(blank=True, default='', max_length=24)),
        migrations.AddField('onlineorder', 'dispatch_notes', models.TextField(blank=True, default='')),
        migrations.AddField('onlineorder', 'return_note', models.CharField(blank=True, default='', max_length=240)),
        migrations.CreateModel(
            name='OnlineOrderEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('kind', models.CharField(max_length=40)),
                ('from_stage', models.CharField(blank=True, max_length=40)),
                ('to_stage', models.CharField(blank=True, max_length=40)),
                ('note', models.TextField(blank=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='shop.onlineorder')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['at', 'pk']},
        ),
    ]
