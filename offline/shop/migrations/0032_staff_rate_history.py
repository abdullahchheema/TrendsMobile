import datetime
import uuid
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def first_rates(apps, schema_editor):
    """Everyone's current rate becomes their first rate, in force from the beginning, so nothing already earned changes."""
    Staff = apps.get_model('shop', 'Staff')
    StaffRate = apps.get_model('shop', 'StaffRate')
    StaffRate.objects.bulk_create([StaffRate(staff=p, rate=p.commission_rate, effective_from=datetime.date(2000, 1, 1)) for p in Staff.objects.all()])


class Migration(migrations.Migration):

    dependencies = [('shop', '0031_confirmation_settings_commission')]

    operations = [
        migrations.CreateModel(
            name='StaffRate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('rate', models.DecimalField(decimal_places=2, max_digits=5)),
                ('effective_from', models.DateField()),
                ('staff', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rates', to='shop.staff')),
            ],
            options={'ordering': ['effective_from']},
        ),
        migrations.AddConstraint(model_name='staffrate', constraint=models.UniqueConstraint(fields=('staff', 'effective_from'), name='staffrate_staff_day')),
        migrations.AddConstraint(model_name='staffrate', constraint=models.CheckConstraint(condition=Q(rate__gte=0, rate__lte=100), name='staffrate_range')),
        migrations.RunPython(first_rates, migrations.RunPython.noop),
    ]
