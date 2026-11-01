from django.db import migrations,models
import django.db.models.deletion

def restore_defaults(apps,schema_editor):
    Staff=apps.get_model('shop','Staff');Warehouse=apps.get_model('shop','Warehouse')
    for person in Staff.objects.all():
        code=person.source.get('warehouse')
        warehouse=Warehouse.objects.filter(code=code,branch_id=person.branch_id,enabled=True).first() if code else None
        if warehouse:person.default_warehouse=warehouse;person.save(update_fields=['default_warehouse'])

class Migration(migrations.Migration):
    dependencies=[('shop','0005_pos_till_reconciliation')]
    operations=[migrations.AddField(model_name='staff',name='default_warehouse',field=models.ForeignKey(to='shop.warehouse',null=True,blank=True,on_delete=django.db.models.deletion.PROTECT)),migrations.RunPython(restore_defaults,migrations.RunPython.noop)]
