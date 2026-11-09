"""Typed, shop-scoped report filters shared by the screen and CSV export."""
from django import forms
from django.utils import timezone
from .models import Account,Warehouse,PhoneModel,Staff,Product,Party
from .access import branches

INVENTORY={'Mobile Inventory','Mobile Stock Ageing','Mobile Device Economics'}
FIELDS={
 'Profit and Loss Statement':['branch','from_date','to_date'],
 'Balance Sheet':['branch','to_date'],
 'Trial Balance':['branch','from_date','to_date'],'Unadjusted Trial Balance':['branch','from_date','to_date'],'Adjusted Trial Balance':['branch','from_date','to_date'],'Cash Flow Statement':['branch','from_date','to_date'],
 'Mobile Inventory':['branch','status','model','warehouse','pta_status'],
 'Mobile Stock Ageing':['branch','warehouse','review_only'],
 'Mobile Device Economics':['branch','warehouse'],
 'Device Profit':['branch','from_date','to_date','model','product','warehouse'],
 'Staff Activity':['branch','from_date','to_date','staff'],
 'Staff Summary':['branch','from_date','to_date','staff'],
 'Purchase Register':['branch','from_date','to_date','party'],
 'Sales Register':['branch','from_date','to_date','party'],
 'Payment Ledger':['branch','from_date','to_date','party','account'],
 'General Ledger':['branch','from_date','to_date','party','account'],
 'Accounts Receivable':['branch','to_date','party'],
 'Accounts Payable':['branch','to_date','party'],
 'Stock Balance':['branch','to_date','warehouse','product'],
 'Stock Ledger':['branch','from_date','to_date','warehouse','product'],
}

class ReportFilters(forms.Form):
    def __init__(self,*args,user,name,**kwargs):
        super().__init__(*args,**kwargs)
        places=branches(user)
        self.fields={
            'branch':forms.ModelChoiceField(label='Shop',queryset=places,required=False,empty_label='All permitted shops'),
            'from_date':forms.DateField(label='From date',required=False,widget=forms.DateInput(attrs={'type':'date'})),
            'to_date':forms.DateField(label='As of date' if name in ('Accounts Receivable','Accounts Payable','Stock Balance','Balance Sheet') else 'To date',required=False,widget=forms.DateInput(attrs={'type':'date'})),
            'status':forms.ChoiceField(label='Stock status',required=False,choices=[('','All statuses')]+[(s,s) for s in ('Available','Sold','Reserved','Unavailable')]),
            'model':forms.ModelChoiceField(queryset=PhoneModel.objects.order_by('name'),required=False,empty_label='All models'),
            'warehouse':forms.ModelChoiceField(label='Shop / warehouse',queryset=Warehouse.objects.filter(branch__in=places).order_by('name'),required=False,empty_label='All warehouses'),
            'staff':forms.ModelChoiceField(label='Staff member',queryset=Staff.objects.filter(branch__in=places).order_by('name'),required=False,empty_label='All staff'),
            'product':forms.ModelChoiceField(label='Variant / accessory',queryset=Product.objects.order_by('name'),required=False,empty_label='All items'),
            'pta_status':forms.ChoiceField(label='PTA status',required=False,choices=[('','All PTA statuses')]+[(s,s) for s in ('PTA','Non-PTA','Not recorded')]),
            'review_only':forms.BooleanField(label='Only phones needing review',required=False),
            'party':forms.ModelChoiceField(label='Customer / supplier',queryset=Party.objects.order_by('name'),required=False,empty_label='All parties'),
            'account':forms.ModelChoiceField(queryset=Account.objects.filter(is_group=False).order_by('name'),required=False,empty_label='All accounts'),
        }
        self.fields={key:self.fields[key] for key in FIELDS.get(name,[])}
        from .reports import empty_columns
        from .access import costs
        self.fields['search']=forms.CharField(label='Search report rows',required=False,max_length=200)
        self.fields['sort_by']=forms.ChoiceField(label='Sort by',required=False,choices=[('','Report order')]+[(column,column) for column in empty_columns(name,costs(user))])
        self.fields['sort_dir']=forms.ChoiceField(label='Sort direction',required=False,choices=[('asc','Ascending'),('desc','Descending')])
        if 'branch' in self.fields:self.fields['branch'].label_from_instance=lambda place:f'{place.name} ({place.code})'
        if 'warehouse' in self.fields:self.fields['warehouse'].label_from_instance=lambda place:f'{place.name} · {place.branch.name} ({place.branch.code})'
        if 'staff' in self.fields:self.fields['staff'].label_from_instance=lambda person:f'{person.name} · {person.branch.name} ({person.branch.code})'
        if 'party' in self.fields:
            kind='Supplier' if name in ('Purchase Register','Accounts Payable') else 'Customer' if name in ('Sales Register','Accounts Receivable') else None
            if kind:self.fields['party'].queryset=self.fields['party'].queryset.filter(kind=kind);self.fields['party'].label=kind
    def clean(self):
        data=super().clean()
        if data.get('from_date') and data.get('to_date') and data['from_date']>data['to_date']:raise forms.ValidationError('From date must not be after To date.')
        for key in ('warehouse','staff'):
            if data.get('branch') and data.get(key) and data[key].branch_id!=data['branch'].pk:self.add_error(key,'Select a record from the selected shop.')
        return data
    def params(self):
        return {key:str(value.pk) if hasattr(value,'pk') else value.isoformat() if hasattr(value,'isoformat') else value for key,value in self.cleaned_data.items() if value not in (None,'')}

def defaults(name):
    today=timezone.localdate()
    result={'from_date':today.replace(day=1).isoformat(),'to_date':today.isoformat()}
    if name=='Mobile Inventory':result['status']='Available'
    if name=='Mobile Stock Ageing':result['review_only']='on'
    return result
