"""Retain omitted native tax templates without repricing or enabling tax rules."""
import hashlib,json
from decimal import Decimal,InvalidOperation
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from shop.models import SourceRecord,ImportRun

EXTRA={'Sales Taxes and Charges Template','Purchase Taxes and Charges Template'}
REVISION='Migration Source Revision'


class Command(BaseCommand):
    help='Compare every original imported source row, then preserve additional native tax-template evidence.'
    def add_arguments(self,parser):
        parser.add_argument('snapshot');parser.add_argument('--sha256',required=True);parser.add_argument('--check-only',action='store_true')
    @transaction.atomic
    def handle(self,*args,**options):
        raw=Path(options['snapshot']).read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest!=options['sha256']:raise CommandError('Snapshot checksum mismatch.')
        data=json.loads(raw)
        if data.get('schema')!=1 or not ImportRun.objects.exists():raise CommandError('An imported baseline and schema-1 export are required.')
        source={}
        for doctype,rows in data['records'].items():
            for row in rows:
                key=(doctype,row['name'])
                if key in source:raise CommandError('Duplicate source identity in export.')
                source[key]=row
        existing={(row.doctype,row.name):row.payload for row in SourceRecord.objects.all()}
        old_core={key:value for key,value in existing.items() if key[0] not in EXTRA|{REVISION}}
        new_core={key:value for key,value in source.items() if key[0] not in EXTRA}
        revisions={}
        if set(old_core)!=set(new_core):raise CommandError('Original source identities changed. Stage and reconcile a fresh full migration; existing business data was not overwritten.')
        for key,old in old_core.items():
            new=new_core[key]
            if old==new:continue
            if key[0]=='Company' and {k:v for k,v in old.items() if k!='modified'}=={k:v for k,v in new.items() if k!='modified'}:
                revisions[key]=new
            else:raise CommandError('Original source records changed. Stage and reconcile a fresh full migration; existing business data was not overwritten.')
        baseline=ImportRun.objects.first().summary;expected=data.get('expected',{})
        try:
            same=set(expected)==set(baseline) and all(Decimal(str(expected[key])).is_finite() and Decimal(str(expected[key]))==Decimal(str(baseline[key])) for key in baseline)
        except (InvalidOperation,TypeError):same=False
        if not same:raise CommandError('Source control totals changed. Reconcile a full staged migration first.')
        additional={key:value for key,value in source.items() if key[0] in EXTRA}
        if any(key in existing and existing[key]!=value for key,value in additional.items()):raise CommandError('Previously preserved source tax settings changed; review a new migration boundary.')
        created=0
        if not options['check_only']:
            for (doctype,name),payload in additional.items():
                _,added=SourceRecord.objects.get_or_create(doctype=doctype,name=name,defaults={'payload':payload});created+=added
            for (doctype,name),payload in revisions.items():
                revision_name=digest+':'+hashlib.sha256((doctype+'\0'+name).encode()).hexdigest()[:32]
                _,added=SourceRecord.objects.get_or_create(doctype=REVISION,name=revision_name,defaults={'payload':{'doctype':doctype,'name':name,'change':'modified timestamp only','snapshot_sha256':digest,'payload':payload}});created+=added
            ImportRun.objects.get_or_create(fingerprint=digest,defaults={'summary':baseline})
        self.stdout.write(json.dumps(dict(source_sha256=digest,core_source_rows_compared=len(old_core),original_source_rows_identical=not revisions,business_source_fields_identical=True,timestamp_only_source_revisions=len(revisions),additional_source_templates=len(additional),source_records_created=created,check_only=options['check_only'],tax_rules_activated=False,business_documents_changed=False),indent=2))
