import uuid
from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import Staff
from .access import branches,staff
from . import services as s,fund_transfers as funds


class FundTransferForm(forms.Form):
    request_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    branch=forms.ModelChoiceField(queryset=None,label='Shop')
    posting_date=forms.DateField(initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    source_method=forms.ChoiceField(label='From payment account')
    target_method=forms.ChoiceField(label='To payment account')
    amount=forms.DecimalField(label='Amount PKR',min_value=Decimal('0.01'),max_digits=15,decimal_places=2)
    handler=forms.ModelChoiceField(queryset=None,label='Handled by')
    cash_scope=forms.ChoiceField(label='Cash custody',choices=[('till','My open POS till'),('shop','Shop cash outside all tills'),('bank','Bank/wallet only (no cash)')],required=False)
    outside_till_confirmed=forms.BooleanField(label='I confirm this cash is outside every POS till',required=False)
    transfer_reference=forms.CharField(label='Actual bank / wallet transaction reference',max_length=180)
    notes=forms.CharField(label='Reason / completed movement details',widget=forms.Textarea(attrs={'rows':3}))
    def __init__(self,*args,user,doc=None,branch=None,**kwargs):
        super().__init__(*args,**kwargs);self.actor=user;self.doc=doc
        places=branches(user);self.fields['branch'].queryset=places
        self.fields['branch'].widget=forms.HiddenInput()
        self.fields['branch'].label_from_instance=lambda item:f'{item.name} ({item.code})'
        if doc:branch=doc.branch
        elif self.is_bound:
            try:branch=places.filter(pk=self.data.get('branch')).first()
            except (ValueError,forms.ValidationError):branch=None
        else:branch=branch or places.first()
        self.branch=branch
        methods=funds.methods(branch) if branch else {}
        labels=[(name,f'{name} · {account.name}') for name,account in methods.items()]
        for key in ('source_method','target_method'):self.fields[key].choices=[('','Select account')]+labels
        self.fields['handler'].queryset=Staff.objects.filter(branch=branch,enabled=True,can_handle_payments=True)
        self.fields['handler'].label_from_instance=lambda item:f'{item.name} · {item.branch.name}'
        person=staff(user);shift=s.active_shift(branch,user) if branch else None
        self.initial.update(branch=branch.pk if branch else None,handler=shift.handler_id if shift else person.pk if person and person.can_handle_payments else None,
            cash_scope='till' if shift or not s.manager(user) else 'shop')
        if doc:
            self.initial.update(posting_date=doc.posting_date,handler=doc.handler_id,notes=doc.notes,**{key:value for key,value in doc.data.items() if key in self.fields})
            self.fields['branch'].disabled=True
            if doc.reference_id:
                for key in ('source_method','target_method','amount'):self.fields[key].disabled=True
        if not s.manager(user):self.fields['cash_scope'].choices=[('till','My open POS till'),('bank','Bank/wallet only (no cash)')]
    def clean(self):
        data=super().clean()
        if data.get('branch') and data.get('source_method') and data.get('target_method') and data.get('amount'):
            funds.validate(data,self.actor,data['branch'])
        return data
