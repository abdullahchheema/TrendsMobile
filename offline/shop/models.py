import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

class Record(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    code=models.CharField(max_length=180,unique=True)
    source=models.JSONField(default=dict,blank=True)
    demo_batch=models.CharField(max_length=300,blank=True)
    created=models.DateTimeField(default=timezone.now,editable=False)
    modified=models.DateTimeField(auto_now=True)
    class Meta:abstract=True
    def __str__(self):return self.code

class Branch(Record):
    name=models.CharField(max_length=180)
    company=models.CharField(max_length=180)
    enabled=models.BooleanField(default=True)
    def __str__(self):return self.name
    def clean(self):
        if not self._state.adding and not self.enabled and Document.objects.filter(branch=self,kind='POS Opening Entry',state='Open',status='Submitted').exists():raise ValidationError('Close this shop’s POS shifts before disabling the shop.')

class Warehouse(Record):
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    name=models.CharField(max_length=180)
    enabled=models.BooleanField(default=True)
    def __str__(self):return self.name
    def clean(self):
        if self._state.adding:return
        previous=Warehouse.objects.get(pk=self.pk)
        linked=StockMovement.objects.filter(warehouse=self).exists() or Device.objects.filter(warehouse=self).exists() or Staff.objects.filter(default_warehouse=self).exists()
        if previous.branch_id!=self.branch_id and linked:raise ValidationError('A warehouse with stock history, devices or staff defaults cannot move to another shop.')
        if not self.enabled:
            from django.db.models import Sum
            stock=StockMovement.objects.filter(warehouse=self).values('product_id').annotate(q=Sum('quantity')).filter(q__gt=0).exists()
            if stock or Device.objects.filter(warehouse=self,status='Available').exists() or Staff.objects.filter(default_warehouse=self,enabled=True).exists():raise ValidationError('Move remaining stock and reassign staff defaults before disabling this warehouse.')

class Till(Record):
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    name=models.CharField(max_length=180)
    enabled=models.BooleanField(default=True)
    def __str__(self):return f'{self.name} · {self.branch.name}'
    def clean(self):
        if not self._state.adding:
            previous=Till.objects.get(pk=self.pk)
            if previous.branch_id!=self.branch_id and Document.objects.filter(till=self).exists():raise ValidationError('A till with transaction history cannot move to another shop.')
            if not self.enabled and Document.objects.filter(till=self,kind='POS Opening Entry',status='Submitted',state='Open').exists():raise ValidationError('Close this till shift before disabling the till.')

class Staff(Record):
    name=models.CharField(max_length=180)
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    default_warehouse=models.ForeignKey(Warehouse,on_delete=models.PROTECT,null=True,blank=True)
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    designation=models.CharField(max_length=100,blank=True)
    enabled=models.BooleanField(default=True)
    manager=models.BooleanField(default=False)
    can_buy=models.BooleanField(default=False)
    can_sell=models.BooleanField(default=False)
    can_handle_payments=models.BooleanField(default=False)
    def __str__(self):return self.name
    def clean(self):
        if self.default_warehouse_id and (self.default_warehouse.branch_id!=self.branch_id or not self.default_warehouse.enabled):raise ValidationError('Select an active default warehouse in this staff member’s shop.')
        if not self._state.adding:
            previous=Staff.objects.get(pk=self.pk)
            changed=previous.branch_id!=self.branch_id or previous.user_id!=self.user_id or (previous.enabled and not self.enabled) or (previous.can_handle_payments and not self.can_handle_payments)
            if changed and Document.objects.filter(handler=self,kind='POS Opening Entry',state='Open',status='Submitted').exists():raise ValidationError('Close this staff member’s POS shift before changing their shop, login or payment access.')

class Party(Record):
    kind=models.CharField(max_length=10,choices=[('Customer','Customer'),('Supplier','Supplier')])
    name=models.CharField(max_length=180)
    city=models.CharField(max_length=100,blank=True)
    phone=models.CharField(max_length=100,blank=True)
    email=models.EmailField(blank=True)
    address=models.TextField(blank=True)
    enabled=models.BooleanField(default=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['kind','name'],name='party_kind_name')]
    def __str__(self):return self.name

class PhoneModel(Record):
    name=models.CharField(max_length=180)
    brand=models.CharField(max_length=100)
    description=models.TextField(blank=True)
    def __str__(self):return self.name

class Product(Record):
    name=models.CharField(max_length=180)
    group=models.CharField(max_length=100,default='Used Phones')
    model=models.ForeignKey(PhoneModel,on_delete=models.PROTECT,null=True,blank=True)
    brand=models.CharField(max_length=100,blank=True)
    storage=models.CharField(max_length=40,blank=True)
    ram=models.CharField(max_length=40,blank=True)
    colour=models.CharField(max_length=80,blank=True)
    serialized=models.BooleanField(default=False)
    stock_item=models.BooleanField(default=True)
    selling_price=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    barcodes=models.JSONField(default=list,blank=True)
    enabled=models.BooleanField(default=True)
    def clean(self):
        if self.serialized and (not self.stock_item or not self.model_id or not self.storage):raise ValidationError('Phone variants require a model, storage and stock tracking.')
        if self.selling_price<0:raise ValidationError('Selling price cannot be negative.')
    def __str__(self):return self.name if self.name==self.code else f'{self.name} ({self.code})'

class Device(Record):
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    product=models.ForeignKey(Product,on_delete=models.PROTECT)
    warehouse=models.ForeignKey(Warehouse,on_delete=models.PROTECT,null=True,blank=True)
    status=models.CharField(max_length=30,default='Available')
    device_type=models.CharField(max_length=30,default='Used')
    condition=models.CharField(max_length=40,default='Not Inspected')
    inspection_status=models.CharField(max_length=30,default='Pending')
    inspection_date=models.DateField(null=True,blank=True)
    battery_checked=models.BooleanField(default=False)
    battery=models.PositiveSmallIntegerField(default=0)
    purchase_cost=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    asking_price=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    minimum_price=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    wholesale_price=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    warranty_days=models.PositiveIntegerField(default=0)
    warranty_terms=models.TextField(blank=True)
    faults=models.TextField(blank=True)
    details=models.JSONField(default=dict,blank=True)
    received_on=models.DateField(default=timezone.localdate)
    sold_on=models.DateField(null=True,blank=True)
    sold_price=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    customer=models.ForeignKey(Party,on_delete=models.PROTECT,null=True,blank=True)
    sale=models.ForeignKey('Document',on_delete=models.PROTECT,null=True,blank=True,related_name='sold_devices')
    def clean(self):
        if self.battery>100:raise ValidationError('Battery health must be between 0 and 100.')
        if min(self.asking_price,self.minimum_price,self.wholesale_price,self.purchase_cost)<0:raise ValidationError('Prices cannot be negative.')
        if self.asking_price and self.asking_price<self.minimum_price:raise ValidationError('Asking price cannot be below minimum price.')
        if self.inspection_status=='Passed' and not self.inspection_date:self.inspection_date=timezone.localdate()
    def __str__(self):return f'{self.code} · {self.product.name}'
    def save(self,*args,**kwargs):
        if not self.branch_id:
            if self.warehouse_id:self.branch=self.warehouse.branch
            elif self.sale_id:self.branch=self.sale.branch
            else:raise ValidationError('A device must belong to a shop.')
        if self.warehouse_id and self.warehouse.branch_id!=self.branch_id:raise ValidationError('Phone warehouse belongs to another shop.')
        return super().save(*args,**kwargs)

class IMEI(models.Model):
    number=models.CharField(primary_key=True,max_length=15)
    device=models.ForeignKey(Device,on_delete=models.CASCADE,related_name='imeis')
    slot=models.PositiveSmallIntegerField()
    pta_status=models.CharField(max_length=30,default='Not Checked')
    verified_on=models.DateField(null=True,blank=True)
    evidence=models.FileField(upload_to='pta/%Y/%m/',blank=True)
    verified_by=models.CharField(max_length=180,blank=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['device','slot'],name='imei_device_slot'),
        models.CheckConstraint(condition=Q(slot__in=[1,2]),name='imei_slot_valid')]
    def clean(self):
        from .services import valid_imei
        if not valid_imei(self.number):raise ValidationError('Enter a valid 15-digit IMEI including its check digit.')
        if self.pta_status!='Not Checked' and not self.verified_on:raise ValidationError('Record the PTA verification date.')

KINDS=['Mobile Intake','Purchase Order','Purchase Receipt','Purchase Invoice','Sales Invoice','POS Invoice',
       'Payment Entry','Journal Entry','Stock Entry','Mobile Exchange','Warranty Claim','Mobile Daily Closing','POS Opening Entry','POS Closing Entry']
class Document(Record):
    kind=models.CharField(max_length=40,choices=[(k,k) for k in KINDS],db_index=True)
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    status=models.CharField(max_length=12,choices=[('Draft','Draft'),('Submitted','Submitted'),('Cancelled','Cancelled')],default='Draft')
    state=models.CharField(max_length=40,blank=True)
    posting_date=models.DateField(default=timezone.localdate,db_index=True)
    customer=models.ForeignKey(Party,on_delete=models.PROTECT,null=True,blank=True,related_name='sales')
    supplier=models.ForeignKey(Party,on_delete=models.PROTECT,null=True,blank=True,related_name='purchases')
    warehouse=models.ForeignKey(Warehouse,on_delete=models.PROTECT,null=True,blank=True)
    reference=models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True,related_name='linked')
    buyer=models.ForeignKey(Staff,on_delete=models.PROTECT,null=True,blank=True,related_name='bought')
    seller=models.ForeignKey(Staff,on_delete=models.PROTECT,null=True,blank=True,related_name='sold')
    handler=models.ForeignKey(Staff,on_delete=models.PROTECT,null=True,blank=True,related_name='handled')
    till=models.ForeignKey(Till,on_delete=models.PROTECT,null=True,blank=True)
    pos_shift=models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True,related_name='shift_transactions')
    staff_names=models.JSONField(default=dict,blank=True)
    total=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    outstanding=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    currency=models.CharField(max_length=3,default='PKR',editable=False)
    is_return=models.BooleanField(default=False)
    notes=models.TextField(blank=True)
    data=models.JSONField(default=dict,blank=True)
    posted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    class Meta:
        ordering=['-posting_date','-created']
        constraints=[models.UniqueConstraint(fields=['branch','posting_date'],condition=Q(kind='Mobile Daily Closing',status='Submitted'),name='one_daily_closing'),
            models.UniqueConstraint(fields=['branch','posted_by'],condition=Q(kind='POS Opening Entry',state='Open',status='Submitted'),name='one_open_shift'),
            models.UniqueConstraint(fields=['till'],condition=Q(kind='POS Opening Entry',state='Open',status='Submitted',till__isnull=False),name='one_open_till'),
            models.UniqueConstraint(fields=['handler'],condition=Q(kind='POS Opening Entry',state='Open',status='Submitted',till__isnull=False),name='one_cashier_shift')]
    def __str__(self):return self.code

class Line(models.Model):
    document=models.ForeignKey(Document,on_delete=models.CASCADE,related_name='lines')
    product=models.ForeignKey(Product,on_delete=models.PROTECT)
    device=models.ForeignKey(Device,on_delete=models.PROTECT,null=True,blank=True)
    warehouse=models.ForeignKey(Warehouse,on_delete=models.PROTECT,null=True,blank=True)
    quantity=models.DecimalField(max_digits=18,decimal_places=3,default=1)
    rate=models.DecimalField(max_digits=18,decimal_places=2)
    cost=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    snapshot=models.JSONField(default=dict,blank=True)

class Account(Record):
    name=models.CharField(max_length=180)
    account_type=models.CharField(max_length=50,blank=True)
    enabled=models.BooleanField(default=True)
    is_group=models.BooleanField(default=False)
    def __str__(self):return f'{self.name} ({self.code})'
    def clean(self):
        if self._state.adding:return
        previous=Account.objects.get(pk=self.pk)
        used=LedgerEntry.objects.filter(account=self).exists() or SalesTaxRule.objects.filter(account=self).exists() or any(str(self.pk) in list(s.accounts.values())+list(s.payment_methods.values()) for s in ShopSettings.objects.all())
        if used and (self.code!=previous.code or self.account_type!=previous.account_type or not self.enabled or self.is_group):raise ValidationError('An account used by a shop or posted transactions must keep its code, type and active posting status.')

class LedgerEntry(models.Model):
    document=models.ForeignKey(Document,on_delete=models.PROTECT,related_name='ledger')
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    posting_date=models.DateField(db_index=True)
    account=models.ForeignKey(Account,on_delete=models.PROTECT)
    party=models.ForeignKey(Party,on_delete=models.PROTECT,null=True,blank=True)
    reference=models.ForeignKey(Document,on_delete=models.PROTECT,null=True,blank=True,related_name='allocations')
    debit=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    credit=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    class Meta:constraints=[models.CheckConstraint(condition=Q(debit__gte=0,credit__gte=0),name='ledger_nonnegative')]

class StockMovement(models.Model):
    document=models.ForeignKey(Document,on_delete=models.PROTECT,related_name='stock')
    posting_date=models.DateField(db_index=True)
    product=models.ForeignKey(Product,on_delete=models.PROTECT)
    device=models.ForeignKey(Device,on_delete=models.PROTECT,null=True,blank=True)
    warehouse=models.ForeignKey(Warehouse,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=18,decimal_places=3)
    value=models.DecimalField(max_digits=18,decimal_places=2)

class ShopSettings(models.Model):
    branch=models.OneToOneField(Branch,on_delete=models.CASCADE,primary_key=True)
    shop_phone=models.CharField(max_length=180,blank=True)
    shop_address=models.TextField(blank=True)
    receipt_terms=models.TextField(blank=True)
    urdu_receipt_note=models.TextField(blank=True)
    ageing_days=models.PositiveIntegerField(default=30)
    accounts=models.JSONField(default=dict)
    payment_methods=models.JSONField(default=dict)

class SalesTaxRule(Record):
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT)
    name=models.CharField(max_length=180)
    rate=models.DecimalField(max_digits=7,decimal_places=4)
    account=models.ForeignKey(Account,on_delete=models.PROTECT)
    reference=models.CharField(max_length=300)
    enabled=models.BooleanField(default=True)
    class Meta:
        ordering=['name','code']
        constraints=[models.CheckConstraint(condition=Q(rate__gte=0,rate__lte=100),name='sales_tax_rate_range')]
    def __str__(self):return f'{self.name} · {self.rate}% · {self.branch.code}'
    def clean(self):
        if not isinstance(self.rate,Decimal) or not self.rate.is_finite() or self.rate<0 or self.rate>100:raise ValidationError('Tax rate must be between 0 and 100, with at most four decimal places.')
        if not (self.reference or '').strip():raise ValidationError('Record the client’s rate/treatment reference.')
        if self.account_id:
            if not self.account.enabled or self.account.is_group or self.account.account_type not in ('Tax','Current Liability'):raise ValidationError('Use a separate active Tax or Current Liability account.')
            if any(str(self.account_id) in list(config.accounts.values())+list(config.payment_methods.values()) for config in ShopSettings.objects.all()):raise ValidationError('Output tax cannot use an existing stock, income, party or payment account.')

class Audit(models.Model):
    created=models.DateTimeField(auto_now_add=True)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True)
    action=models.CharField(max_length=80)
    document=models.ForeignKey(Document,on_delete=models.PROTECT,null=True)
    details=models.JSONField(default=dict)

class RequestKey(models.Model):
    key=models.UUIDField(primary_key=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    fingerprint=models.CharField(max_length=64)
    document=models.ForeignKey(Document,on_delete=models.PROTECT,null=True)

class SourceRecord(models.Model):
    doctype=models.CharField(max_length=100)
    name=models.CharField(max_length=180)
    payload=models.JSONField()
    class Meta:constraints=[models.UniqueConstraint(fields=['doctype','name'],name='source_doctype_name')]

class ImportRun(models.Model):
    fingerprint=models.CharField(max_length=64,unique=True)
    created=models.DateTimeField(auto_now_add=True)
    summary=models.JSONField()

class SupabaseIdentity(models.Model):
    auth_id=models.UUIDField(unique=True)
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='supabase_identity')
