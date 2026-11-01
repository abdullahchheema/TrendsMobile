from django.db import migrations,models
import django.db.models.deletion

def fill(apps,schema_editor):
    Device=apps.get_model('shop','Device');Branch=apps.get_model('shop','Branch')
    for device in Device.objects.select_related('warehouse','sale').all():
        branch=device.warehouse.branch_id if device.warehouse_id else device.sale.branch_id if device.sale_id else None
        if not branch:
            source_company=device.source.get('company')
            branch=Branch.objects.filter(company=source_company).values_list('pk',flat=True).first()
        if not branch:raise RuntimeError('Review the shop assignment of '+device.code)
        device.branch_id=branch;device.save(update_fields=['branch'])

class Migration(migrations.Migration):
    dependencies=[('shop','0001_initial')]
    operations=[migrations.AddField(model_name='device',name='branch',field=models.ForeignKey(null=True,on_delete=django.db.models.deletion.PROTECT,to='shop.branch')),
        migrations.RunPython(fill,migrations.RunPython.noop),
        migrations.AlterField(model_name='device',name='branch',field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='shop.branch'))]
