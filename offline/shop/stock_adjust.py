"""Manager screen for stock write-offs and count corrections (posts through services._adjust)."""
import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import render,redirect
from django.utils import timezone
from .models import Branch,Device,Document,Line,Product,Warehouse
from . import access,services as s


class _Invalid(Exception):pass


class StockAdjustForm(forms.Form):
    def __init__(self,*args,user,branch,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['request_key']=forms.UUIDField(widget=forms.HiddenInput(),initial=uuid.uuid4)
        self.fields['branch']=forms.ModelChoiceField(queryset=access.branches(user),initial=branch,widget=forms.HiddenInput())
        places=Warehouse.objects.filter(branch=branch,enabled=True)
        from . import shopdefaults
        self.default_warehouse=shopdefaults.warehouse(branch);self.fields['warehouse']=forms.ModelChoiceField(queryset=places,initial=self.default_warehouse.pk,required=False,widget=forms.HiddenInput())
        self.fields['reason']=forms.ChoiceField(label='Reason',choices=[('','Choose a reason')]+[(r,r) for r in s.ADJUST_REASONS])
        phones=Device.objects.filter(branch=branch,status__in=s.WRITE_OFF_FROM,warehouse__isnull=False).select_related('product').prefetch_related('imeis').order_by('product__name')      # also phones that came back damaged or are still waiting to be checked
        self.fields['phone']=forms.ModelChoiceField(label='Write off a phone',queryset=phones,required=False,empty_label='— not a phone —')
        self.fields['phone'].label_from_instance=lambda d:f'{d.product.name} · '+(('IMEI '+' / '.join(i.number for i in d.imeis.all())) if d.imeis.all() else d.code)+f' · cost PKR {d.purchase_cost:,.0f}'+('' if d.status=='Available' else ' · damaged, not for sale' if d.status=='Unavailable' else ' · in Holding')
        self.fields['product']=forms.ModelChoiceField(label='Or adjust accessory / other stock',queryset=Product.objects.filter(enabled=True,stock_item=True,serialized=False).order_by('name'),required=False,empty_label='— not an accessory —')
        self.fields['change']=forms.DecimalField(label='Change in quantity (use − to remove)',required=False,max_digits=12,decimal_places=3,help_text='Example: -2 removes two damaged units; 3 adds three units found in a count.')
        self.fields['unit_cost']=forms.DecimalField(label='Unit cost (only when adding stock that has no history)',required=False,min_value=Decimal('0'),max_digits=15,decimal_places=2)
        self.fields['posting_date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['notes']=forms.CharField(label='Note',required=False,widget=forms.Textarea(attrs={'rows':2}))

    def clean(self):
        data=super().clean()
        if self.errors:return data
        if bool(data.get('phone'))==bool(data.get('product')):raise ValidationError('Choose either one phone to write off or one accessory product to adjust.')
        if data.get('product') and not data.get('change'):raise ValidationError('Enter the change in quantity.')
        data['warehouse']=data['phone'].warehouse if data.get('phone') and data['phone'].warehouse_id else self.default_warehouse
        return data


@transaction.atomic
def post(data,user):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk)
    if not access.manager(user):raise PermissionDenied('A manager must adjust stock.')
    access.require(user,branch)
    doc=s.new_doc('Stock Adjustment',branch,warehouse=data['warehouse'],posting_date=data['posting_date'],notes=data.get('notes',''),data={'reason':data['reason']})
    if data.get('phone'):
        Line.objects.create(document=doc,product=data['phone'].product,device=data['phone'],warehouse=data['warehouse'],quantity=-1,rate=0)
    else:
        Line.objects.create(document=doc,product=data['product'],warehouse=data['warehouse'],quantity=data['change'],rate=data.get('unit_cost') or 0)
    return s.submit(doc,user)


@login_required
def adjust(request):
    from .views import selected_branch,error_text
    from .views import ask_which_shop
    gate=ask_which_shop(request,'Stock adjustment','Choose the shop whose stock you are adjusting.')
    if gate:return gate
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    if not access.manager(request.user):raise PermissionDenied('A manager must adjust stock.')
    form=StockAdjustForm(request.POST or None,user=request.user,branch=branch)
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                if not form.is_valid():raise _Invalid()
                return post(form.cleaned_data,request.user)
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            doc=s.once(key,request.user,{'stock_adjust':payload},perform)
            direction='out of' if Decimal(doc.data.get('loss','0'))>0 else 'into'
            messages.success(request,f'Stock adjustment {doc.code} posted: PKR {doc.total:,.2f} moved {direction} stock value.')
            return redirect(f'{request.path}?branch={branch.pk}')
        except _Invalid:pass
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    recent=Document.objects.filter(branch=branch,kind='Stock Adjustment').order_by('-posting_date','-created')[:15]
    return render(request,'stock_adjust.html',dict(title='Stock adjustments',branch=branch,form=form,recent=recent))
