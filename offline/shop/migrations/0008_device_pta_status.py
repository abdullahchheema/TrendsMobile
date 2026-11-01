from django.db import migrations,models


def preserve_known_pta(apps,schema_editor):
    Device=apps.get_model('shop','Device');IMEI=apps.get_model('shop','IMEI')
    for device in Device.objects.all().iterator():
        statuses=list(IMEI.objects.filter(device_id=device.pk).values_list('pta_status',flat=True))
        value='Non-PTA' if any(status in ('Non-compliant','Blocked') for status in statuses) else 'PTA' if statuses and all(status=='Compliant' for status in statuses) else 'Not recorded'
        Device.objects.filter(pk=device.pk).update(pta_status=value)


class Migration(migrations.Migration):
    dependencies=[('shop','0007_sales_tax_rule')]
    operations=[migrations.AddField(model_name='device',name='pta_status',field=models.CharField(max_length=20,default='Not recorded',db_default='Not recorded',choices=[('PTA','PTA'),('Non-PTA','Non-PTA'),('Not recorded','Not recorded')])),migrations.RunPython(preserve_known_pta,migrations.RunPython.noop)]
