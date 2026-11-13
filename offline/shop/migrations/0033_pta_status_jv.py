from django.db import migrations, models


class Migration(migrations.Migration):
    """JV (a phone imported from Japan, usually not PTA-registered) joins PTA and Non-PTA as a choice. Only the list of allowed values changes; no data is touched."""

    dependencies = [('shop', '0032_staff_rate_history')]

    operations = [
        migrations.AlterField(
            model_name='device',
            name='pta_status',
            field=models.CharField(choices=[('PTA', 'PTA'), ('Non-PTA', 'Non-PTA'), ('JV', 'JV'), ('Not recorded', 'Not recorded')], db_default='Not recorded', default='Not recorded', max_length=20),
        ),
    ]
