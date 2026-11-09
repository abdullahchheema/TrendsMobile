"""Owner-managed, explicitly approved exclusive sales-tax rules; no preset rates."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404,render,redirect
from .models import Account,Branch,SalesTaxRule,Document,Audit
from . import access,setup,services


class TaxRuleForm(forms.ModelForm):
    class Meta:
        model=SalesTaxRule
        fields=['name','rate','account','reference','enabled']
        labels={'rate':'Exclusive rate (%)','account':'Output-tax liability account','reference':'Client-approved rate / treatment reference'}
        help_texts={'rate':'Applied to the subtotal after invoice discount. No statutory rate is supplied by the system.',
            'reference':'Record the client’s instruction or reviewed tax treatment. This rule applies to the entire invoice.'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['account'].queryset=Account.objects.filter(enabled=True,is_group=False,account_type__in=['Tax','Current Liability'])
        self.fields['rate'].widget.attrs.update(min='0',max='100',step='0.0001')


@login_required
def listing(request):
    setup.owner(request.user)
    return render(request,'tax_rules.html',dict(title='Sales tax rules',rules=SalesTaxRule.objects.filter(branch__in=access.branches(request.user)).select_related('branch','account')))


@login_required
def edit(request,pk=None):
    setup.owner(request.user)
    rule=get_object_or_404(SalesTaxRule,pk=pk,branch__in=access.branches(request.user)) if pk else None
    if rule:branch=rule.branch
    else:
        branch=get_object_or_404(access.branches(request.user),pk=request.GET.get('branch') or access.branches(request.user).values_list('pk',flat=True).first())
        rule=SalesTaxRule(branch=branch)
    form=TaxRuleForm(request.POST or None,instance=rule)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                Branch.objects.select_for_update(no_key=True).get(pk=branch.pk)
                if Document.objects.filter(branch=branch,kind='POS Opening Entry',state='Open',status='Submitted').exists():raise ValidationError('Close this shop’s POS shifts before changing sales-tax rules.')
                if pk:SalesTaxRule.objects.select_for_update().get(pk=pk)
                saved=form.save(commit=False)
                saved.account=Account.objects.select_for_update().get(pk=saved.account_id)
                if not saved.code:saved.code=services.code('TAX')
                saved.full_clean();saved.save()
                Audit.objects.create(user=request.user,action='Save sales tax rule',branch=branch,details={'rule':str(saved.pk),'branch':str(branch.pk),'name':saved.name,'rate':str(saved.rate),'account':str(saved.account_id),'reference':saved.reference,'enabled':saved.enabled})
            messages.success(request,'Sales-tax rule saved. Existing submitted invoices keep their original tax.');return redirect('tax_rules')
        except ValidationError as error:form.add_error(None,error)
    return render(request,'form.html',dict(title=f'Sales tax · {branch.name}',form=form,master=True))
