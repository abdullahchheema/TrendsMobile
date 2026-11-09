from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Customers and suppliers belong to one shop. The old (kind, name) rule is replaced so the same person can exist in two shops
    as two separate records; records that belong to no shop yet (legacy, unused) stay unique by name among themselves."""
    dependencies = [('shop', '0018_advanced_accounting')]
    operations = [
        migrations.AddField(
            model_name='party', name='branch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='parties', to='shop.branch',
                                    help_text='The shop this customer or supplier belongs to. Empty only for old records that were never used.')),
        migrations.RemoveConstraint(model_name='party', name='party_kind_name'),
        migrations.AddConstraint(model_name='party', constraint=models.UniqueConstraint(fields=('branch', 'kind', 'name'), name='party_branch_kind_name')),
        migrations.AddConstraint(model_name='party', constraint=models.UniqueConstraint(fields=('kind', 'name'), condition=models.Q(('branch__isnull', True)), name='party_unassigned_kind_name')),
    ]
