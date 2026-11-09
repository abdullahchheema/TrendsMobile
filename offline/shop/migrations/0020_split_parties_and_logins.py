from collections import Counter
from django.conf import settings
from django.db import migrations
from django.db.models import Count


def split_parties(apps, schema_editor):
    """Give every used customer/supplier to the shop that used it most; clone it (same details, limit and phone) for every other shop
    that used it, and repoint that shop's documents, ledger rows and sold phones to the clone."""
    Party = apps.get_model('shop', 'Party')
    Document = apps.get_model('shop', 'Document')
    LedgerEntry = apps.get_model('shop', 'LedgerEntry')
    Device = apps.get_model('shop', 'Device')
    for party in Party.objects.filter(branch__isnull=True).iterator():
        used = Counter()
        for model, field in ((Document, 'customer'), (Document, 'supplier'), (LedgerEntry, 'party'), (Device, 'customer')):
            for row in model.objects.filter(**{field: party}).values('branch_id').annotate(n=Count('pk')):
                used[row['branch_id']] += row['n']
        if not used:
            continue
        ranked = [b for b, _ in used.most_common()]
        party.branch_id = ranked[0]
        party.save(update_fields=['branch'])
        for number, branch_id in enumerate(ranked[1:], 2):
            code = f'{party.code}-S{number}'
            while Party.objects.filter(code=code).exists():
                number += 1
                code = f'{party.code}-S{number}'
            clone = Party.objects.create(
                code=code, name=party.name, kind=party.kind, city=party.city, phone=party.phone, email=party.email, address=party.address,
                enabled=party.enabled, credit_limit=party.credit_limit, demo_batch=party.demo_batch, source=party.source, branch_id=branch_id)
            Document.objects.filter(branch_id=branch_id, customer=party).update(customer=clone)
            Document.objects.filter(branch_id=branch_id, supplier=party).update(supplier=clone)
            LedgerEntry.objects.filter(branch_id=branch_id, party=party).update(party=clone)
            Device.objects.filter(branch_id=branch_id, customer=party).update(customer=clone)
            for doc in Document.objects.filter(branch_id=branch_id, data__trade_in_customer=str(party.pk)):
                doc.data = dict(doc.data, trade_in_customer=str(clone.pk))
                doc.save(update_fields=['data'])


def one_login_per_shop(apps, schema_editor):
    """Exactly one login per shop. Keep the shop's own login (or its best existing one) and make it a full shop login;
    deactivate and unlink every other login so no old limited user silently becomes a shop account."""
    Branch = apps.get_model('shop', 'Branch')
    Staff = apps.get_model('shop', 'Staff')
    for branch in Branch.objects.all():
        linked = list(Staff.objects.filter(branch=branch, user__isnull=False).select_related('user').order_by('created'))
        if not linked:
            continue
        live = [s for s in linked if s.enabled and s.user.is_active and not s.user.is_superuser]
        keep = (next((s for s in live if s.code.endswith('-SHOP')), None) or next((s for s in live if s.manager), None)
                or (live[0] if live else None))
        for person in linked:
            if keep is not None and person.pk == keep.pk:
                person.manager = person.can_buy = person.can_sell = person.can_handle_payments = True
                person.save(update_fields=['manager', 'can_buy', 'can_sell', 'can_handle_payments'])
            elif keep is not None and not person.user.is_superuser:
                user = person.user
                user.is_active = False
                user.save(update_fields=['is_active'])
                person.user = None
                person.enabled = False
                person.save(update_fields=['user', 'enabled'])


class Migration(migrations.Migration):
    dependencies = [('shop', '0019_party_branch'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RunPython(split_parties, migrations.RunPython.noop),
        migrations.RunPython(one_login_per_shop, migrations.RunPython.noop),
    ]
