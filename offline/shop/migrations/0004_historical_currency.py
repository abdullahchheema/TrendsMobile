from django.db import migrations
def preserve(apps,schema_editor):
    Document=apps.get_model('shop','Document')
    for doc in Document.objects.exclude(source={}):
        original=doc.source.get('currency')
        if original and len(original)==3 and doc.currency!=original:
            doc.currency=original;doc.save(update_fields=['currency'])
class Migration(migrations.Migration):
    dependencies=[('shop','0003_supabase_identity')]
    operations=[migrations.RunPython(preserve,migrations.RunPython.noop)]
