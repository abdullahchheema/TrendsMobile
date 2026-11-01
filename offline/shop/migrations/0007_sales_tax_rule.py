import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('shop','0006_staff_default_warehouse')]
    operations=[migrations.CreateModel(name='SalesTaxRule',fields=[
        ('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
        ('code',models.CharField(max_length=180,unique=True)),
        ('source',models.JSONField(blank=True,default=dict)),
        ('demo_batch',models.CharField(blank=True,max_length=300)),
        ('created',models.DateTimeField(default=django.utils.timezone.now,editable=False)),
        ('modified',models.DateTimeField(auto_now=True)),
        ('name',models.CharField(max_length=180)),
        ('rate',models.DecimalField(decimal_places=4,max_digits=7)),
        ('reference',models.CharField(max_length=300)),
        ('enabled',models.BooleanField(default=True)),
        ('account',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='shop.account')),
        ('branch',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='shop.branch')),
    ],options={'ordering':['name','code'],'constraints':[models.CheckConstraint(condition=models.Q(('rate__gte',0),('rate__lte',100)),name='sales_tax_rate_range')]})]
