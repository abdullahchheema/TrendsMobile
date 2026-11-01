import uuid
from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import Branch,Warehouse,Product,Device,Staff,ShopSettings
from . import access,easy_retail,services
from .customer_widgets import CustomerNameInput


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
        super().__init__(*args,**kwargs);self.kind=kind
        config=ShopSettings.objects.filter(branch=branch).first();people=Staff.objects.filter(branch=branch,enabled=True)
        self.fields['request_key']=forms.UUIDField(widget=forms.HiddenInput(),initial=uuid.uuid4)
        self.fields['branch']=forms.ModelChoiceField(queryset=access.branches(user),initial=branch,widget=forms.HiddenInput())
        self.fields['party_name']=forms.CharField(label='Customer name' if kind=='sale' else 'Who are you buying from?',max_length=180,widget=CustomerNameInput(attrs={'placeholder':'Type full name','autocomplete':'off'}) if kind=='sale' else forms.TextInput(attrs={'placeholder':'Type supplier / seller name'}))
        if kind=='sale':
            phones=Device.objects.filter(branch=branch,status='Available',warehouse__isnull=False,warehouse__enabled=True,product__enabled=True).select_related('product','warehouse')
            self.fields['phone']=forms.ModelChoiceField(label='Phone to sell',queryset=phones,empty_label='Choose a phone from stock',initial=phone,error_messages={'invalid_choice':'This phone is no longer available. Choose another phone.'})
            self.fields['phone'].label_from_instance=lambda value:f'{value.product.name} · {value.pta_status} · PKR {value.asking_price:,.0f} · {value.code}'
        else:
            self.fields['product']=forms.ModelChoiceField(label='Phone model / storage / colour',queryset=Product.objects.filter(enabled=True,serialized=True).select_related('model').order_by('name'),empty_label='Choose phone variant')
            self.fields['quantity']=forms.IntegerField(label='How many phones?',min_value=1,max_value=20,initial=1)
        self.fields['pta_status']=forms.ChoiceField(label='PTA status',choices=[('','Choose PTA status'),('PTA','PTA'),('Non-PTA','Non-PTA')],initial=phone.pta_status if phone and phone.pta_status in ('PTA','Non-PTA') else '')
        self.fields['price']=forms.DecimalField(label='Selling price (PKR)' if kind=='sale' else 'Buying price per phone (PKR)',min_value=Decimal('0.01'),max_digits=15,decimal_places=2,initial=phone.asking_price if phone else None)
        if kind=='purchase':
            self.fields['asking_price']=forms.DecimalField(label='Expected selling price per phone (PKR)',min_value=Decimal('0.01'),max_digits=15,decimal_places=2)
            self.fields['device_type']=forms.ChoiceField(label='Phone type',choices=[(x,x) for x in ('Used','New','Refurbished')],initial='Used')
            self.fields['condition']=forms.ChoiceField(label='Condition',choices=[(x,x) for x in ('Good','Excellent','Very Good','Fair','For Parts')],initial='Good')
        person=access.staff(user);can_pay=person is None or person.manager or person.can_handle_payments
        choices=[('full','Paid in full'),('part','Part payment'),('unpaid','Pay later / on credit')] if can_pay else [('unpaid','Pay later / payment collected separately')]
        self.fields['payment_status']=forms.ChoiceField(label='Payment',choices=choices,initial='full' if can_pay else 'unpaid')
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
        self.fields['notes']=forms.CharField(label='Note (optional)',required=False,widget=forms.Textarea(attrs={'rows':2}))
        self.main_fields=[self[name] for name in ('party_name','phone','product','quantity','pta_status','price','asking_price','device_type','condition') if name in self.fields]
        self.payment_fields=[self[name] for name in ('payment_status','amount_paid','method','payment_reference')]
        self.extra_fields=[self[name] for name in ('staff','handler','posting_date','warehouse','notes') if name in self.fields]

    def clean(self):
        data=super().clean()
        if data.get('branch'):
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
            if paid and config and data.get('method') not in services.cash_modes(data['branch']) and not data.get('payment_reference'):self.add_error('payment_reference','Enter the bank or wallet transaction reference.')
        return data
