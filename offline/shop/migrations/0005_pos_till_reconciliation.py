import uuid
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

def seed_tills(apps,schema_editor):
    Branch=apps.get_model('shop','Branch');Till=apps.get_model('shop','Till')
    for branch in Branch.objects.all():
        Till.objects.get_or_create(code='TILL-'+str(branch.pk),defaults={'branch':branch,'name':'Main till'})

class Migration(migrations.Migration):
    dependencies=[('shop','0004_historical_currency')]
    operations=[
        migrations.CreateModel(name='Till',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
            ('code',models.CharField(max_length=180,unique=True)),
            ('source',models.JSONField(default=dict,blank=True)),('demo_batch',models.CharField(max_length=300,blank=True)),
            ('created',models.DateTimeField(default=django.utils.timezone.now,editable=False)),('modified',models.DateTimeField(auto_now=True)),
            ('name',models.CharField(max_length=180)),('enabled',models.BooleanField(default=True)),
            ('branch',models.ForeignKey(to='shop.branch',on_delete=django.db.models.deletion.PROTECT))]),
        migrations.AddField(model_name='document',name='till',field=models.ForeignKey(to='shop.till',null=True,blank=True,on_delete=django.db.models.deletion.PROTECT)),
        migrations.AddField(model_name='document',name='pos_shift',field=models.ForeignKey(to='shop.document',null=True,blank=True,on_delete=django.db.models.deletion.PROTECT,related_name='shift_transactions')),
        migrations.AddConstraint(model_name='document',constraint=models.UniqueConstraint(fields=['till'],condition=models.Q(kind='POS Opening Entry',state='Open',status='Submitted',till__isnull=False),name='one_open_till')),
        migrations.AddConstraint(model_name='document',constraint=models.UniqueConstraint(fields=['handler'],condition=models.Q(kind='POS Opening Entry',state='Open',status='Submitted',till__isnull=False),name='one_cashier_shift')),
        migrations.RunPython(seed_tills,migrations.RunPython.noop),
    ]
