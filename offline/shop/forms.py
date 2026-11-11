from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from .models import *
from .access import branches,manager,costs
from . import access
from .services import balances,till_balance
from . import customer_names
from .customer_widgets import CustomerNameInput,name_input

DATE=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})
DETAIL_FIELDS=['manufacturer_serial','regional_version','market_description','display_condition','biometrics','camera_test','charging_test',
    'audio_test','network_test','carrier_lock','activation_lock','sim_configuration','repair_history','included_accessories','inspection_notes','pta_notes']
DETAIL_CHOICES={
    'display_condition':['Not Checked','Original / Working','Replaced / Working','Defective'],
    'biometrics':['Not Checked','Working','Not Working','Not Applicable'],
    'carrier_lock':['Not Checked','Factory Unlocked','Carrier Locked'],
    'activation_lock':['Not Checked','Off','On'],
    'sim_configuration':['Not Checked','Physical SIM','Physical SIM + eSIM','Dual Physical SIM','eSIM Only'],
    **{key:['Not Checked','Passed','Failed'] for key in ('camera_test','charging_test','audio_test','network_test')},
}
DETAIL_LABELS={'biometrics':'Face ID / Touch ID','activation_lock':'Activation Lock / iCloud','audio_test':'Speaker / Microphone','network_test':'Calls / Mobile Network'}
def detail_field(key,initial=''):
    if key in DETAIL_CHOICES:
        choices=DETAIL_CHOICES[key]+([initial] if initial and initial not in DETAIL_CHOICES[key] else [])
        return forms.ChoiceField(label=DETAIL_LABELS.get(key,key.replace('_',' ').title()),required=False,choices=[('','Not recorded')]+[(v,v) for v in choices],initial=initial)
    return forms.CharField(required=False,initial=initial,widget=forms.Textarea(attrs={'rows':2}) if key in ('repair_history','included_accessories','inspection_notes','pta_notes') else forms.TextInput)

def sections(form):
    groups=[('Shop, transaction and staff',['branch','posting_date','customer','customer_name','supplier','warehouse','reference','buyer','seller','handler','till','purchase_source','trade_in_customer','trade_in_customer_name','seller_phone']),
        ('Cash count and settlement',['opening_float','counted_cash','difference_notes','mode','transaction_reference','direction','amount','sales_invoice','purchase_invoice','target_warehouse']),
        ('Invoice discount and sales tax',['invoice_discount_type','invoice_discount_value','sales_tax_rule']),
        ('Phone details',['device_type','pta_status','manufacturer_serial','regional_version','market_description','imei_1','pta_status_1','verified_on_1','evidence_1','imei_2','pta_status_2','verified_on_2','evidence_2','pta_notes']),
        ('Condition and inspection',['condition','grade','inspection_status','inspection_date','battery_checked','battery',*DETAIL_CHOICES,'repair_history','included_accessories','inspection_notes','faults']),
        ('Prices and checking warranty',['asking_price','online_price','minimum_price','wholesale_price','warranty_days','warranty_terms']),
        ('Claim and notes',['device','claim_status','resolution','notes'])]
    if getattr(form,'kind',None)=='Stock Entry':groups=[('Source, destination and date',['branch','posting_date','warehouse','target_warehouse']),('Transfer reason',['notes'])]
    result=[];seen=set()
    for title,names in groups:
        fields=[form[name] for name in names if name in form.fields and name not in seen]
        seen.update(field.name for field in fields)
        if fields:result.append(dict(title=title,fields=fields))
    extra=[form[name] for name in form.fields if name not in seen]
    if extra:result.append(dict(title='Balance counts',fields=extra))
    return result
class DeviceForm(forms.ModelForm):
    class Meta:
        model=Device
        fields=['device_type','pta_status','condition','grade','inspection_status','inspection_date','battery_checked','battery','asking_price','online_price','minimum_price','wholesale_price','warranty_days','warranty_terms','faults']
        widgets={'inspection_date':DATE}
        labels={'asking_price':'Shop price (PKR)','online_price':'Online price (PKR, optional)'}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        if not costs(user):
            for key in ('minimum_price','wholesale_price'):self.fields.pop(key)
        for key in DETAIL_FIELDS:self.fields[key]=detail_field(key,self.instance.details.get(key,''))
        self.fields['pta_status'].choices=[('','Choose PTA status'),('PTA','PTA'),('Non-PTA','Non-PTA')]
        self.fields['online_price'].help_text='Leave empty to use the product online price, then the shop price.'
        self.sections=sections(self)
    def clean_asking_price(self):
        value=self.cleaned_data['asking_price']
        if value is None or value<=0:raise forms.ValidationError('Enter the shop selling price: every phone needs one.')
        return value

class DocumentForm(forms.ModelForm):
    class Meta:
        model=Document
        fields=['branch','posting_date','customer','supplier','warehouse','reference','buyer','seller','handler','till','notes']
        widgets={'posting_date':DATE,'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,user,kind,**kwargs):
        super().__init__(*args,**kwargs);self.kind=kind;self.user=user
        places=branches(user);self.fields['branch'].queryset=places
        self.fields['branch'].label_from_instance=lambda place:f'{place.name} ({place.code})'
        branch=self.instance.branch if self.instance.branch_id else places.first()
        selected=self.data.get('branch') if self.is_bound else None
        if selected:branch=places.filter(pk=selected).first()
        if branch:self.fields['branch'].initial=branch;self.initial['branch']=branch.pk
        self.fields['customer'].queryset=access.parties_for(branch,'Customer').filter(enabled=True)
        self.fields['supplier'].queryset=access.parties_for(branch,'Supplier').filter(enabled=True)
        self.fields['warehouse'].queryset=Warehouse.objects.filter(branch__in=places,enabled=True)
        self.fields['warehouse'].label_from_instance=lambda place:f'{place.name} · {place.branch.name} ({place.branch.code})'
        people=Staff.objects.filter(branch__in=places,enabled=True)
        for name,capability in [('buyer','can_buy'),('seller','can_sell'),('handler','can_handle_payments')]:
            self.fields[name].queryset=people.filter(**{capability:True})
            self.fields[name].label_from_instance=lambda person:f'{person.name} · {person.branch.name} ({person.branch.code})'
            linked=self.fields[name].queryset.filter(user=user).first()
            if linked:
                self.fields[name].initial=linked
                if not getattr(self.instance,name+'_id',None):self.initial[name]=linked.pk
        person=people.filter(user=user,branch=branch).select_related('default_warehouse').first()
        if person and person.default_warehouse_id and person.default_warehouse.enabled and 'warehouse' in self.fields and not self.instance.warehouse_id:self.initial['warehouse']=person.default_warehouse_id
        keep={'branch','posting_date','notes'}
        if kind in ('Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order'):keep|={'supplier','warehouse','buyer'}
        if kind in ('Purchase Invoice','Purchase Receipt','Sales Invoice','POS Closing Entry'):keep.add('reference')
        if kind in ('Sales Invoice','POS Invoice'):keep|={'customer','warehouse','seller'}
        if kind=='Payment Entry':keep|={'customer','supplier','reference','handler'}
        if kind=='Stock Entry':keep.add('warehouse')
        if kind=='Mobile Exchange':keep|={'customer','handler'}
        if kind in ('Mobile Daily Closing','POS Opening Entry','POS Closing Entry'):keep.add('handler')
        if kind=='POS Opening Entry':keep.add('till')
        if kind=='Warranty Claim':keep|={'customer','reference'}
        for name in list(self.fields):
            if name not in keep:self.fields.pop(name)
        if 'reference' in self.fields:
            docs=Document.objects.filter(branch__in=places,status='Submitted')
            if kind=='Purchase Invoice':docs=docs.filter(kind='Purchase Receipt')
            elif kind=='Purchase Receipt':docs=docs.filter(kind='Purchase Order')
            elif kind=='POS Closing Entry':
                docs=docs.filter(kind='POS Opening Entry',state='Open')
                if not manager(user):docs=docs.filter(posted_by=user)
            elif kind in ('Sales Invoice','Warranty Claim'):docs=docs.filter(kind__in=['Sales Invoice','POS Invoice'],is_return=False)
            else:docs=docs.filter(kind__in=['Sales Invoice','POS Invoice','Purchase Invoice'])
            self.fields['reference'].queryset=docs
            if kind=='Payment Entry':self.fields['reference'].label_from_instance=lambda doc:f'{doc.code} · {doc.customer or doc.supplier or ""} · Outstanding PKR {doc.outstanding}'
        def add(name,field):self.fields[name]=field
        self.pos_reconciliation=None
        if kind in ('Sales Invoice','POS Invoice') and not self.instance.is_return:
            spec=self.instance.data.get('adjustments',{})
            add('invoice_discount_type',forms.ChoiceField(label='Discount type',choices=[('amount','PKR amount'),('percent','Percentage')],initial=spec.get('discount_type','amount'),required=False))
            add('invoice_discount_value',forms.DecimalField(label='Invoice discount',min_value=0,decimal_places=2,initial=spec.get('discount_value','0'),required=False,help_text='Applied before tax. Percentage must not exceed 100.'))
            add('sales_tax_rule',forms.ModelChoiceField(label='Sales tax',queryset=SalesTaxRule.objects.filter(branch=branch,enabled=True,account__enabled=True,account__is_group=False),required=False,initial=spec.get('tax_rule') or None,empty_label='No tax added',help_text='Only client-approved rules configured by the owner are available. Saving captures the current rule version.'))
        if kind=='POS Opening Entry':
            self.fields['till'].queryset=Till.objects.filter(branch=branch,enabled=True)
            self.fields['till'].required=True
            add('opening_float',forms.DecimalField(label='Counted opening cash (PKR)',min_value=0,decimal_places=2,help_text='Count the cash physically placed in this till. This records custody; it does not create an accounting receipt.'))
        if kind=='POS Closing Entry':
            add('counted_cash',forms.DecimalField(label='Physically counted closing cash (PKR)',min_value=0,decimal_places=2))
            add('difference_notes',forms.CharField(label='Cash difference / legacy review explanation',required=False,widget=forms.Textarea(attrs={'rows':3})))
            selected=self.data.get('reference') if self.is_bound else self.instance.reference_id
            try:shift=self.fields['reference'].queryset.filter(pk=selected).first() if selected else None
            except (ValueError,ValidationError):shift=None
            if shift:self.pos_reconciliation=till_balance(shift)
        if kind=='Mobile Intake':
            add('purchase_source',forms.ChoiceField(choices=[(x,x) for x in ('Distributor','Walk-in Seller','Customer Trade-In','Other')]))
            add('trade_in_customer',forms.ModelChoiceField(queryset=access.parties_for(branch,'Customer'),required=False))
            for name,options in [('device_type',['New','Used','Refurbished','Repaired']),('condition',['Excellent','Very Good','Good','Fair','Not Inspected','For Parts']),('inspection_status',['Pending','Passed','Repair Needed','Failed'])]:
                add(name,forms.ChoiceField(choices=[(x,x) for x in options],initial={'device_type':'Used','condition':'Not Inspected','inspection_status':'Pending'}[name]))
            add('inspection_date',forms.DateField(required=False,widget=DATE));add('battery_checked',forms.BooleanField(required=False))
            add('battery',forms.IntegerField(min_value=0,max_value=100,required=False))
            for name in ('asking_price','minimum_price','wholesale_price'):add(name,forms.DecimalField(min_value=0,decimal_places=2,required=False))
            add('warranty_days',forms.IntegerField(min_value=0,required=False))
            for name in ('warranty_terms','faults'):add(name,forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2})))
            for name in DETAIL_FIELDS:add(name,detail_field(name))
            add('pta_status',forms.ChoiceField(label='PTA status',choices=[('','Choose PTA status'),('PTA','PTA'),('Non-PTA','Non-PTA')]))
        if kind in ('Payment Entry','Mobile Exchange'):
            methods=ShopSettings.objects.filter(branch=branch).first() if branch else None
            add('mode',forms.ChoiceField(choices=[(x,x) for x in (methods.payment_methods if methods else ['Cash'])]))
            add('transaction_reference',forms.CharField(required=False))
        if kind=='Payment Entry':
            add('direction',forms.ChoiceField(choices=[('Receive','Receive'),('Pay','Pay')]))
            add('amount',forms.DecimalField(min_value=Decimal('0.01'),decimal_places=2))
        if kind=='Stock Entry':
            add('target_warehouse',forms.ModelChoiceField(label='Destination warehouse',queryset=Warehouse.objects.filter(branch__in=places,enabled=True)))
            self.fields['target_warehouse'].label_from_instance=lambda place:f'{place.name} · {place.branch.name} ({place.branch.code})'
            self.fields['warehouse'].required=True;self.fields['warehouse'].label='Source warehouse'
        if kind=='Mobile Exchange':
            for name,kinds in [('sales_invoice',['Sales Invoice','POS Invoice']),('purchase_invoice',['Purchase Invoice'])]:
                add(name,forms.ModelChoiceField(queryset=Document.objects.filter(branch__in=places,status='Submitted',kind__in=kinds,is_return=False)))
        if kind=='Warranty Claim':
            add('device',forms.ModelChoiceField(queryset=Device.objects.filter(sale__branch__in=places)))
            add('claim_status',forms.ChoiceField(choices=[(x,x) for x in ['Open','Work In Progress','Closed','Cancelled']]))
            add('resolution',forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':3})))
        if kind=='Mobile Daily Closing' and branch:
            date=self.data.get('posting_date') or timezone.localdate()
            try:rows=balances(branch,date)
            except (ValueError,ValidationError):rows=[]
            for row in rows:
                add('count_'+row['account'],forms.DecimalField(label=f"{row['name']} — expected PKR {row['expected']}",decimal_places=2))
                add('note_'+row['account'],forms.CharField(label='Difference explanation',required=False))
        if self.instance.pk and self.instance.code:
            initial=self.instance.data.copy()
            if self.kind=='Mobile Intake' and isinstance(initial.get('device'),dict):
                device=initial['device'];initial.update(device);initial.update(device.get('details',{}))
                for slot,value in enumerate(device.get('imeis',[]),1):initial['imei_'+str(slot)]=value.get('number','')
            for name,field in self.fields.items():
                if name in initial:field.initial=initial[name]
        if self.instance.is_return:
            self.fields['warehouse'].required=True;self.fields['warehouse'].label='Receiving warehouse'
            for name in ('branch','customer','seller','reference'):
                if name in self.fields:self.fields[name].disabled=True
            if 'seller' in self.fields:
                self.fields['seller'].queryset=Staff.objects.filter(pk=self.instance.seller_id);self.fields['seller'].required=False
            if 'customer' in self.fields:self.fields['customer'].queryset=Party.objects.filter(pk=self.instance.customer_id)
        self.typed_customers={}
        if not self.instance.is_return:
            for field in ('customer','trade_in_customer'):
                if field not in self.fields:continue
                name_field=field+'_name';self.typed_customers[field]=name_field
                self.fields[field].widget=forms.HiddenInput()
                existing=getattr(self.instance,field,None) if field=='customer' else None
                if field=='trade_in_customer' and self.instance.data.get(field):
                    existing=access.parties_for(branch,'Customer').filter(pk=self.instance.data[field]).first()
                self.fields[name_field]=forms.CharField(label='Customer name' if field=='customer' else 'Trade-in customer name',required=False,max_length=180,
                    initial=existing.name if existing else '',widget=name_input(branch,attrs={'autocomplete':'off','placeholder':'Type customer name','required':kind in ('Sales Invoice','POS Invoice') and field=='customer'}),
                    help_text='Type a new name to add a customer, or an existing name to reuse their account.')
        self.hide_shop_internals(branch,user)
        self.sections=sections(self)
    HIDDEN=('buyer','seller','handler','warehouse','till')
    def defaults_for(self,branch):
        from . import shopdefaults
        shop=shopdefaults.ensure(branch);me=shopdefaults.person(self.user,branch)
        return dict(buyer=me,seller=me,handler=me,warehouse=shop['warehouse'],till=shop['till'])
    def hide_shop_internals(self,branch,user):
        """Staff, warehouse and till are never asked for: the shop (or the login) fills them in."""
        if not branch:return
        defaults=self.defaults_for(branch)
        for name in self.HIDDEN:
            if name not in self.fields or (self.instance.is_return and name=='seller'):continue
            self.fields[name].widget=forms.HiddenInput();self.fields[name].required=False
            if not getattr(self.instance,name+'_id',None):self.initial[name]=defaults[name].pk
    def clean(self):
        result=super().clean()
        branch=result.get('branch')
        if branch:
            defaults=self.defaults_for(branch)
            for name in self.HIDDEN:
                if name in self.fields and not (self.instance.is_return and name=='seller'):
                    value=result.get(name)
                    if not value or value.branch_id!=branch.pk:result[name]=defaults[name]
            for name in ('warehouse','buyer','seller','handler','reference'):
                value=result.get(name)
                if value and value.branch_id!=branch.pk:self.add_error(name,'Select a record from this transaction’s shop.')
        for field,name_field in self.typed_customers.items():
            name=result.get(name_field,'')
            if name:
                try:
                    result[name_field]=customer_names.clean_name(name)
                    result[field]=customer_names.find(name,result.get(field),branch=branch)
                except ValidationError as error:self.add_error(name_field,error)
            elif name_field in self.data:result[field]=None
        if self.kind in ('Sales Invoice','POS Invoice') and not self.instance.is_return and not result.get('customer') and not result.get('customer_name'):
            self.add_error('customer_name','Enter a customer name.')
        if self.kind=='Payment Entry' and result.get('customer_name') and result.get('supplier'):
            self.add_error('customer_name','Enter a customer or select a supplier, not both.')
        if self.kind=='Mobile Intake' and result.get('purchase_source')=='Customer Trade-In' and not (result.get('trade_in_customer') or result.get('trade_in_customer_name')):
            self.add_error('trade_in_customer_name','Enter the customer trading in this phone.')
        if self.kind=='Stock Entry':
            source=result.get('warehouse');target=result.get('target_warehouse')
            if source and target and source.pk==target.pk:self.add_error('target_warehouse','Select a different destination warehouse.')
            if target and branch and target.branch_id!=branch.pk and not result.get('notes','').strip():self.add_error('notes','Record the reason for this inter-shop transfer.')
        return result
    def apply_customers(self,doc,user):
        for field,name_field in self.typed_customers.items():
            name=self.cleaned_data.get(name_field)
            if name:
                customer=customer_names.resolve(name,user,self.cleaned_data.get(field),branch=doc.branch)
                self.cleaned_data[field]=customer
                if field=='customer':doc.customer=customer

    def apply_data(self,doc):
        values=self.cleaned_data
        extras={name:(str(value.pk) if hasattr(value,'pk') else value.isoformat() if hasattr(value,'isoformat') else str(value) if isinstance(value,Decimal) else value)
            for name,value in values.items() if name not in self._meta.fields and name not in self.typed_customers.values() and not name.startswith(('count_','note_'))}
        extras['reference']=extras.pop('transaction_reference','')
        if self.kind=='Mobile Intake':
            doc.data=dict(purchase_source=extras.get('purchase_source'),trade_in_customer=extras.get('trade_in_customer'),
                device={**extras,'details':{name:extras.get(name,'') for name in DETAIL_FIELDS},
                    'imeis':[{'number':extras.get(f'imei_{slot}','')} for slot in (1,2)]})
        else:doc.data.update(extras)
        if self.kind in ('Sales Invoice','POS Invoice') and not doc.is_return:
            from .pricing import inputs
            rule=values.get('sales_tax_rule')
            inputs(doc,values['invoice_discount_type'],values.get('invoice_discount_value') or '0',rule.pk if rule else '',capture=True)
            for key in ('invoice_discount_type','invoice_discount_value','sales_tax_rule'):doc.data.pop(key,None)
        if self.kind=='Mobile Daily Closing':doc.data['counts']={name[6:]:dict(counted=str(value),notes=values.get('note_'+name[6:],'')) for name,value in values.items() if name.startswith('count_')}
        if self.kind=='Warranty Claim':doc.state=values.get('claim_status','Open')

class LineForm(forms.ModelForm):
    class Meta:
        model=Line
        fields=['product','device','warehouse','quantity','rate']
    def __init__(self,*args,user=None,kind=None,is_return=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['product'].queryset=Product.objects.filter(enabled=True).order_by('name')
        if kind=='Stock Entry':
            self.fields['product'].queryset=self.fields['product'].queryset.filter(stock_item=True)
            self.fields['rate'].initial=0;self.fields['rate'].disabled=True;self.fields['rate'].required=False
            self.fields['rate'].widget=forms.HiddenInput()
        if kind=='Mobile Intake':self.fields['product'].queryset=self.fields['product'].queryset.filter(serialized=True)
        devices=Device.objects.all()
        if kind!='Purchase Invoice':devices=devices.filter(Q(status='Available')|Q(pk=self.instance.device_id))
        self.fields['device'].queryset=devices.select_related('product').order_by('code')
        if user:
            places=branches(user)
            self.fields['warehouse'].queryset=Warehouse.objects.filter(branch__in=places,enabled=True)
            self.fields['warehouse'].label_from_instance=lambda place:f'{place.name} · {place.branch.name} ({place.branch.code})'
            self.fields['device'].queryset=self.fields['device'].queryset.filter(branch__in=places)
        if is_return:
            self.fields['product'].queryset=Product.objects.filter(pk=self.instance.product_id)
            for name in ('product','device','rate','warehouse'):self.fields[name].disabled=True
            self.fields['warehouse'].widget=forms.HiddenInput()
            self.fields['quantity'].widget.attrs.update(min='0.001',step='0.001')
            if self.instance.product_id and self.instance.product.serialized:self.fields['quantity'].disabled=True

LineSet=inlineformset_factory(Document,Line,form=LineForm,extra=1,can_delete=True)
ReturnLineSet=inlineformset_factory(Document,Line,form=LineForm,extra=0,can_delete=True)

MASTER_MODELS={'Mobile Model':(PhoneModel,['code','name','brand','description']),
    'Item':(Product,['code','name','group','model','brand','storage','ram','colour','serialized','stock_item','reorder_level','selling_price','enabled']),
    'Customer':(Party,['name','city','phone','email','address','credit_limit','enabled']),
    'Supplier':(Party,['name','city','phone','email','address','enabled']),
    'Mobile Staff':(Staff,['code','name','branch','user','designation','enabled','manager','can_buy','can_sell','can_handle_payments']),
    'Warehouse':(Warehouse,['code','name','branch','enabled']),
    'Till':(Till,['code','name','branch','enabled']),
    'Account':(Account,['code','name','account_type','enabled']),
    'Shop':(Branch,['code','name','company','enabled'])}
