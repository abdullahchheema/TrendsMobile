import uuid
from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import Branch,Warehouse,Product,Device,Staff,ShopSettings
from . import access,catalog_entry,easy_retail,services,shopdefaults
from .customer_widgets import CustomerNameInput,name_input


class SalesAccountsForm(forms.Form):
    def __init__(self,*args,user,branch,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['branch']=forms.ModelChoiceField(label='Shop',queryset=access.branches(user),initial=branch)
        self.fields['from_date']=forms.DateField(label='Sales from',required=False,initial='2000-01-01',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['to_date']=forms.DateField(label='Through / balances as of',required=False,initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    def clean(self):
        data=super().clean()
        if data.get('from_date') and data.get('to_date') and data['from_date']>data['to_date']:raise forms.ValidationError('The start date must be before or equal to the end date.')
        return data


class EasyEntryForm(forms.Form):
    def __init__(self,*args,user,branch,kind,phone=None,**kwargs):
        super().__init__(*args,**kwargs);self.kind=kind;self.user=user
        config=ShopSettings.objects.filter(branch=branch).first();people=Staff.objects.filter(branch=branch,enabled=True)
        self.fields['request_key']=forms.UUIDField(widget=forms.HiddenInput(),initial=uuid.uuid4)
        self.fields['branch']=forms.ModelChoiceField(queryset=access.branches(user),initial=branch,widget=forms.HiddenInput())
        self.fields['party_name']=forms.CharField(label='Customer name' if kind=='sale' else 'Who are you buying from?',max_length=180,widget=name_input(branch,attrs={'placeholder':'Type full name','autocomplete':'off'}) if kind=='sale' else forms.TextInput(attrs={'placeholder':'Type supplier / seller name'}))
        if kind=='sale':
            phones=Device.objects.filter(branch=branch,status='Available',warehouse__isnull=False,warehouse__enabled=True,product__enabled=True).select_related('product','warehouse').prefetch_related('imeis')
            self.fields['find_imei']=forms.CharField(label='Find by IMEI (optional)',required=False,widget=forms.TextInput(attrs={'placeholder':'Type or scan an IMEI to narrow the list','autocomplete':'off','inputmode':'numeric','data-imei-filter':'phone'}))
            self.fields['phone']=forms.ModelChoiceField(label='Phone to sell',queryset=phones,empty_label='Choose a phone from stock',initial=phone,error_messages={'invalid_choice':'This phone is no longer available. Choose another phone.'})
            self.fields['phone'].label_from_instance=lambda value:f'{value.product.name} · {value.pta_status} · '+(f'PKR {value.asking_price:,.0f} · ' if value.asking_price else '')+(('IMEI '+' / '.join(i.number for i in value.imeis.all())) if value.imeis.all() else value.code)
        else:
            self.fields['brand']=forms.CharField(label='Brand',max_length=80,required=False,widget=forms.TextInput(attrs={'list':'phone-brands','autocomplete':'off','placeholder':'Choose or type, e.g. Apple, Samsung'}))
            self.fields['model']=forms.CharField(label='Model',max_length=150,required=False,widget=forms.TextInput(attrs={'list':'phone-models','autocomplete':'off','placeholder':'Choose or type, e.g. 14 Pro Max'}))
            self.fields['storage']=forms.ChoiceField(label='Storage',required=False,choices=[('','Choose storage')]+[(x,x) for x in catalog_entry.STORAGE])
            self.fields['colour']=forms.CharField(label='Colour',max_length=80,required=False,widget=forms.TextInput(attrs={'list':'phone-colours','autocomplete':'off','placeholder':'e.g. Grey'}))
            self.fields['product']=forms.ModelChoiceField(queryset=Product.objects.filter(enabled=True,serialized=True),required=False,widget=forms.HiddenInput())
            self.fields['quantity']=forms.IntegerField(label='How many phones?',min_value=1,max_value=20,initial=1)
            self.fields['imeis']=forms.CharField(label='IMEI numbers (recommended)',required=False,widget=forms.Textarea(attrs={'rows':3,'placeholder':'One phone per line. Dual-SIM: put both IMEIs on the line, separated by a space.','autocomplete':'off'}),
                help_text='Entering IMEIs lets you find each phone later and stops the same phone being bought twice. Leave blank only if you will add them later.')
        self.fields['pta_status']=forms.ChoiceField(label='PTA status',choices=[('','Choose PTA status'),('PTA','PTA approved'),('Non-PTA','Non-PTA')],initial=phone.pta_status if phone and phone.pta_status in ('PTA','Non-PTA') else '')
        self.fields['price']=forms.DecimalField(label='Selling price (PKR)' if kind=='sale' else 'Buying price per phone (PKR)',min_value=Decimal('0.01'),max_digits=15,decimal_places=2,initial=(phone.asking_price or None) if phone else None)
        if kind=='purchase':
            self.fields['asking_price']=forms.DecimalField(label='Selling price per phone (PKR)',min_value=Decimal('0.01'),max_digits=15,decimal_places=2,help_text='The price this phone is listed at. The cashier can still change it at the sale.')
            self.fields['device_type']=forms.ChoiceField(label='Phone type',choices=[(x,x) for x in ('Used','New','Refurbished')],initial='Used')
            self.fields['condition']=forms.ChoiceField(label='Condition',choices=[(x,x) for x in ('Good','Excellent','Very Good','Fair','For Parts')],initial='Good')
            self.fields['phone_notes']=forms.CharField(label='Notes about the phone (optional)',required=False,max_length=400,widget=forms.Textarea(attrs={'rows':2,'placeholder':'e.g. battery changed, display replaced, back glass cracked'}))
        person=access.staff(user);can_pay=person is None or person.manager or person.can_handle_payments
        choices=[('full','Paid in full'),('part','Part payment'),('unpaid','Pay later / on credit')] if can_pay else [('unpaid','Pay later / payment collected separately')]
        self.fields['payment_status']=forms.ChoiceField(label='Payment',choices=choices,initial='full' if can_pay else 'unpaid',help_text='Choose “Pay later / on credit” when no money is paid now. The remaining amount is recorded automatically.')
        self.fields['amount_paid']=forms.DecimalField(label='Amount paid now (PKR)',min_value=0,max_digits=15,decimal_places=2,required=False)
        self.fields['method']=forms.ChoiceField(label='Payment method',choices=[(x,x) for x in (config.payment_methods if config else [])],initial='Cash',required=False)
        self.fields['payment_reference']=forms.CharField(label='Bank / wallet transaction reference',required=False,max_length=180)
        capability='can_sell' if kind=='sale' else 'can_buy';staff=people.filter(**{capability:True})
        self.fields['staff']=forms.ModelChoiceField(label='Sold by' if kind=='sale' else 'Bought by',queryset=staff,initial=staff.filter(user=user).first() or staff.first())
        shift=services.active_shift(branch,user);handlers=people.filter(can_handle_payments=True)
        self.fields['handler']=forms.ModelChoiceField(label='Payment handled by',queryset=handlers,required=False,initial=shift.handler if shift else handlers.filter(user=user).first() or handlers.first())
        self.fields['posting_date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        if kind=='purchase':
            places=Warehouse.objects.filter(branch=branch,enabled=True);default=person.default_warehouse if person and person.default_warehouse_id else places.first()
            self.fields['warehouse']=forms.ModelChoiceField(label='Keep stock at',queryset=places,initial=default)
        if kind=='sale':
            self.fields['due_date']=forms.DateField(label='Customer will pay the balance by',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),help_text='For credit or part-payment sales. Overdue balances show in Overdue payments.')
            self.fields['instalments']=forms.IntegerField(label='Split the balance into monthly instalments',required=False,min_value=0,max_value=24,initial=0,help_text='0 = one payment on the date above.')
            self.fields['override_approver']=forms.CharField(label='Manager override: manager code',required=False,max_length=40,widget=forms.TextInput(attrs={'autocomplete':'off'}),help_text='Only needed for a price below the minimum.')
            self.fields['override_pin']=forms.CharField(label='Manager PIN',required=False,max_length=8,widget=forms.PasswordInput(render_value=False,attrs={'autocomplete':'new-password','inputmode':'numeric'}))
        self.fields['notes']=forms.CharField(label='Note (optional)',required=False,widget=forms.Textarea(attrs={'rows':2}))
        shop=shopdefaults.ensure(branch);self.shop_defaults=dict(staff=shopdefaults.person(user,branch),handler=shopdefaults.person(user,branch),warehouse=shop['warehouse'])
        for name,default in self.shop_defaults.items():       # nobody is asked who handled it or which warehouse: the shop fills these in
            if name in self.fields:self.fields[name].widget=forms.HiddenInput();self.fields[name].required=False;self.fields[name].initial=default.pk
        self.main_fields=[self[name] for name in ('party_name','find_imei','phone','brand','model','storage','colour','quantity','imeis','pta_status','device_type','condition','phone_notes','price','asking_price') if name in self.fields]
        self.payment_fields=[self[name] for name in ('payment_status','amount_paid','method','due_date','instalments') if name in self.fields]
        self.extra_fields=[self[name] for name in ('staff','handler','posting_date','warehouse','override_approver','override_pin','notes') if name in self.fields]

    def clean(self):
        data=super().clean()
        if data.get('branch'):
            for name,default in self.shop_defaults.items():
                value=data.get(name)
                if name in self.fields and (not value or value.branch_id!=data['branch'].pk):
                    data[name]=shopdefaults.warehouse(data['branch']) if name=='warehouse' else shopdefaults.person(self.user,data['branch'])
            for name in ('phone','warehouse','staff','handler'):
                value=data.get(name)
                if value and value.branch_id!=data['branch'].pk:self.add_error(name,'Choose a record from this shop.')
        if data.get('price') and data.get('payment_status'):
            total=data['price']*(data.get('quantity') or 1)
            try:paid=easy_retail.paid_amount(data,total)
            except forms.ValidationError as error:self.add_error('amount_paid',error);paid=0
            if paid and not data.get('handler'):self.add_error('handler','Choose who handled the payment.')
            if paid and not data.get('method'):self.add_error('method','Choose a payment method.')
            config=ShopSettings.objects.filter(branch=data.get('branch')).first()
        if self.kind=='purchase' and not data.get('product'):
            try:data['variant']=catalog_entry.check({k:data.get(k) for k in ('brand','model','storage','colour')})
            except forms.ValidationError as error:
                for key,label in (('brand','brand'),('model','model'),('storage','storage'),('colour','colour')):
                    if not str(data.get(key) or '').strip():self.add_error(key,f'Enter the {label}.')
                if not self.errors:self.add_error(None,error)
        if self.kind=='purchase' and 'imeis' not in self.errors and 'quantity' not in self.errors:
            try:data['imeis']=easy_retail.parse_imeis(data.get('imeis',''),data.get('quantity') or 1)
            except forms.ValidationError as error:self.add_error('imeis',error)
        return data
