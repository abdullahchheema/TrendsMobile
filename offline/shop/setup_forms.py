from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError
from .models import Branch,Staff,Warehouse,Account,LedgerEntry,Role
from .access import branches
from .setup import POSTING,METHODS

class CreateShopForm(forms.Form):
    code=forms.RegexField(label='Shop code',regex=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$',max_length=30,help_text='A unique short code, such as SHOP-02.')
    name=forms.CharField(label='Shop name',max_length=180)
    company=forms.CharField(label='Business / company name',max_length=180)
    shop_phone=forms.CharField(label='Contact phone',max_length=180,required=False)
    shop_address=forms.CharField(label='Location / address',required=False,widget=forms.Textarea(attrs={'rows':2}))
    warehouse_name=forms.CharField(label='Initial warehouse name',max_length=180,initial='Stock room')
    till_name=forms.CharField(label='Initial till name',max_length=180,initial='Main till')
    payment_methods=forms.MultipleChoiceField(choices=[(v,v) for v in METHODS],initial=['Cash'],widget=forms.CheckboxSelectMultiple)
    def clean_code(self):
        code=self.cleaned_data['code'].upper()
        if Branch.objects.filter(code__iexact=code).exists():raise ValidationError('This shop code is already in use.')
        return code
    def clean_payment_methods(self):
        value=self.cleaned_data['payment_methods']
        if 'Cash' not in value:raise ValidationError('Include Cash for the shop till.')
        return value

class PostingAccountsForm(forms.Form):
    def __init__(self,*args,settings,**kwargs):
        super().__init__(*args,**kwargs)
        accounts=Account.objects.filter(enabled=True,is_group=False).order_by('name')
        for key,(label,kind) in POSTING.items():
            query=accounts.filter(account_type=kind) if key in ('receivable','payable') else accounts.exclude(account_type__in=['Receivable','Payable','Cash','Bank'])
            self.fields[key]=forms.ModelChoiceField(label=label,queryset=query,initial=settings.accounts.get(key))
        self.fields['inter_shop']=forms.ModelChoiceField(label='Inter-shop clearing (optional)',queryset=accounts.filter(account_type='Current Asset'),initial=settings.accounts.get('inter_shop'),required=False,
            help_text='Use a separate Current Asset account for transfers between shops. Locked after its first posting.')
    def mapping(self):return {key:str(value.pk) for key,value in self.cleaned_data.items() if value is not None}

class PaymentMethodForm(forms.Form):
    label=forms.CharField(label='Payment method',max_length=80)
    account=forms.ModelChoiceField(label='Cash / bank / wallet account',queryset=Account.objects.none())
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['account'].queryset=Account.objects.filter(enabled=True,is_group=False,account_type__in=['Cash','Bank']).order_by('name')

PaymentMethods=formset_factory(PaymentMethodForm,extra=1,can_delete=True,max_num=30,validate_max=True)
def payment_mapping(formset):
    result={}
    for form in formset:
        if not form.cleaned_data or form.cleaned_data.get('DELETE'):continue
        name=form.cleaned_data['label'].strip()
        if name.casefold() in [label.casefold() for label in result]:raise ValidationError('Payment method names must be unique.')
        result[name]=str(form.cleaned_data['account'].pk)
    return result

ROLES={
 'manager':('Shop manager',(True,True,True,True)),
 'sales_payments':('Sales & collections',(False,False,True,True)),
 'sales':('Sales',(False,False,True,False)),
 'buyer':('Purchases',(False,True,False,False)),
 'buyer_payments':('Purchases & supplier payments',(False,True,False,True)),
 'cashier':('Cashier',(False,False,False,True)),
 'technician':('Technician',(False,False,False,False)),
}
class StaffSetupForm(forms.ModelForm):
    role=forms.ModelChoiceField(label='Role',queryset=Role.objects.none(),to_field_name='code',required=False,empty_label='No role - set responsibilities below',help_text='A role sets what this person may do. Editing roles: Settings > Roles & permissions.')
    approval_pin=forms.RegexField(label='Manager override PIN (optional)',regex=r'^\d{4,8}$',required=False,widget=forms.PasswordInput(render_value=False,attrs={'autocomplete':'new-password','inputmode':'numeric'}),help_text='4-8 digits. Lets a manager approve a discount, a price below minimum or a refund at the counter. Leave blank to keep the current PIN.')
    class Meta:
        model=Staff
        fields=['code','name','branch','default_warehouse','designation','role','user','enabled','manager','can_buy','can_sell','can_handle_payments']
    def save(self,commit=True):
        pin=self.cleaned_data.get('approval_pin')
        if pin:
            from django.contrib.auth.hashers import make_password
            self.instance.approval_pin=make_password(pin)
        return super().save(commit)
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs);self.actor=user
        self.fields['role'].queryset=Role.objects.filter(enabled=True).exclude(code='owner')
        self.fields['code'].required=False
        self.fields['branch'].queryset=branches(user)
        raw=self.data.get('branch') if self.is_bound else self.instance.branch_id
        try:branch=branches(user).filter(pk=raw).first() if raw else branches(user).first()
        except (ValueError,ValidationError):branch=None
        self.fields['default_warehouse'].queryset=Warehouse.objects.filter(branch=branch,enabled=True) if branch else Warehouse.objects.none()
        self.fields['default_warehouse'].label='Default warehouse'
        if branch and not self.is_bound:self.initial['branch']=branch.pk
    def clean(self):
        data=super().clean();role=data.get('role')
        if role:
            perms=set(role.permissions)
            data.update(manager='manage_branch' in perms,can_buy='buy' in perms,can_sell='sell' in perms,can_handle_payments='payments' in perms)
            if not data.get('designation'):data['designation']=role.name
        if self.instance.user_id==self.actor.pk and not self.actor.is_superuser:
            if not data.get('manager') or not data.get('enabled') or data.get('user')!=self.actor:raise ValidationError('The owner must review changes to your own manager/login access.')
        return data

class AccountSetupForm(forms.ModelForm):
    class Meta:
        model=Account
        fields=['code','name','account_type','root_type','parent','is_group','enabled']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['parent'].queryset=Account.objects.filter(is_group=True,enabled=True).exclude(pk=self.instance.pk).order_by('code')
        self.fields['parent'].label='Sits under (group)';self.fields['root_type'].label='Section';self.fields['is_group'].label='This is a group (holds other accounts, cannot be posted to)'
        kinds=sorted({kind for label,kind in POSTING.values()}|{'Cash','Bank','Current Asset','Fixed Asset','Accumulated Depreciation','Depreciation','Tax','Current Liability','Equity'}|({self.instance.account_type} if self.instance.account_type else set()))
        self.fields['account_type']=forms.ChoiceField(choices=[('','Select account type')]+[(v,v) for v in kinds],required=False,initial=self.instance.account_type)
        if not self.instance._state.adding and LedgerEntry.objects.filter(account=self.instance).exists():
            self.fields['code'].disabled=True;self.fields['account_type'].disabled=True;self.fields['enabled'].disabled=True;self.fields['root_type'].disabled=True;self.fields['is_group'].disabled=True
    def clean(self):
        data=super().clean()
        if not data.get('is_group') and not data.get('account_type'):self.add_error('account_type','Select an account type.')
        if not data.get('root_type') and not data.get('parent'):
            from .financial_reports import TYPES
            data['root_type']=TYPES.get(data.get('account_type'),'')
        return data
