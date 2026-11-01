import uuid
from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import Branch
from . import journals


class JournalForm(forms.Form):
    request_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    branch=forms.ModelChoiceField(queryset=Branch.objects.none(),widget=forms.HiddenInput)
    posting_date=forms.DateField(initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    supporting_reference=forms.CharField(label='Supporting reference',max_length=180)
    notes=forms.CharField(label='Adjustment / correction reason',widget=forms.Textarea(attrs={'rows':3}))
    def __init__(self,*args,places,branch,doc=None,**kwargs):
        super().__init__(*args,**kwargs);self.fields['branch'].queryset=places;self.initial['branch']=branch.pk
        if doc:
            self.fields['branch'].disabled=True
            self.initial.update(posting_date=doc.posting_date,supporting_reference=doc.data['manual_journal'].get('supporting_reference',''),notes=doc.notes)


class JournalRow(forms.Form):
    account=forms.ModelChoiceField(queryset=None)
    debit=forms.DecimalField(label='Debit PKR',min_value=0,max_digits=15,decimal_places=2,required=False)
    credit=forms.DecimalField(label='Credit PKR',min_value=0,max_digits=15,decimal_places=2,required=False)
    memo=forms.CharField(label='Row note',max_length=180,required=False)
    def __init__(self,*args,branch,protected=False,**kwargs):
        super().__init__(*args,**kwargs);self.fields['account'].queryset=journals.accounts(branch)
        if protected:
            for name in ('account','debit','credit','memo'):self.fields[name].disabled=True


class Rows(forms.BaseFormSet):
    def __init__(self,*args,protected=False,**kwargs):
        super().__init__(*args,**kwargs)
        if protected:
            for form in self.forms:
                if 'DELETE' in form.fields:form.fields['DELETE'].disabled=True
    def rows(self):
        return [dict(account=str(form.cleaned_data['account'].pk),debit=str(form.cleaned_data.get('debit') or Decimal(0)),credit=str(form.cleaned_data.get('credit') or Decimal(0)),memo=form.cleaned_data.get('memo',''))
            for form in self.forms if form.cleaned_data and not form.cleaned_data.get('DELETE') and form.cleaned_data.get('account')]


JournalRows=forms.formset_factory(JournalRow,formset=Rows,extra=2,can_delete=True,max_num=30,validate_max=True,absolute_max=60)
