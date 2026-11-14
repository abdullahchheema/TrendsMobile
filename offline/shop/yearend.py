"""Year-end close: move the period's income and expenses into retained earnings and lock the period."""
from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import Account,Branch,Document,LedgerEntry
from . import access,financial_reports,permissions,services as s

ZERO=Decimal('0.00')


def _movements(branch,start,end):
    """Net debit-minus-credit of every income and expense account in the period, ignoring earlier closing entries."""
    entries=financial_reports.pre_closing(LedgerEntry.objects.filter(branch=branch,posting_date__gte=start,posting_date__lte=end))
    classes=financial_reports.classifications()
    rows=[]
    for r in entries.values('account_id').annotate(dr=Sum('debit'),cr=Sum('credit')):
        account,root=classes[r['account_id']];net=(r['dr'] or ZERO)-(r['cr'] or ZERO)
        if root=='Unclassified' and net:raise ValidationError(f'Account {account.code} must be classified before the year can be closed.')
        if root in ('Income','Expense') and net:rows.append((account,root,net))
    return sorted(rows,key=lambda x:x[0].code)


def preview(branch,start,end):
    rows=_movements(branch,start,end)
    return dict(rows=[dict(account=a,root=root,amount=-net if root=='Income' else net) for a,root,net in rows],profit=-sum((net for _,_,net in rows),ZERO))


@transaction.atomic
def close_year(user,branch,start,end,retained):
    if not permissions.can(user,'accounting'):raise PermissionDenied('You do not have permission to close a period.')
    access.require(user,branch)
    branch=Branch.objects.select_for_update().get(pk=branch.pk)
    if start>end:raise ValidationError('The start date must not be after the closing date.')
    if end>timezone.localdate():raise ValidationError('A year cannot be closed after today.')
    if retained.root_type!='Equity' or retained.is_group or not retained.enabled:raise ValidationError('Choose an active equity account for retained earnings.')
    if Document.objects.filter(branch=branch,kind='Journal Entry',data__year_end_close=True,status='Submitted',data__to__gte=str(end)).exists():
        raise ValidationError('This period has already been closed.')
    earlier=Document.objects.filter(branch=branch,kind='Journal Entry',data__year_end_close=True,status='Submitted',data__to__gte=str(start)).order_by('-data__to').first()
    if earlier:       # the profit of those days is already in retained earnings; closing them again would move it a second time
        raise ValidationError(f'This shop is already closed through {date.fromisoformat(earlier.data["to"]):%d %b %Y}. Start the new period on the day after that.')
    rows=_movements(branch,start,end)
    if not rows:raise ValidationError('There is no income or expense in this period to close.')
    doc=s.new_doc('Journal Entry',branch,posting_date=end,status='Submitted',posted_by=user,notes=f'Year-end closing {start:%d %b %Y} to {end:%d %b %Y}',data={'year_end_close':True,'from':str(start),'to':str(end),'retained_earnings':str(retained.pk)})
    profit=ZERO
    for account,root,net in rows:
        if net<0:s.entry(doc,account,debit=-net)       # credit balance (income): debit it to zero
        else:s.entry(doc,account,credit=net)           # debit balance (expense): credit it to zero
        profit-=net
    if profit>0:s.entry(doc,retained,credit=profit)
    elif profit<0:s.entry(doc,retained,debit=-profit)
    s.balanced(doc)
    if not branch.lock_through or branch.lock_through<end:Branch.objects.filter(pk=branch.pk).update(lock_through=end)
    s.audit(user,'Year-end close',doc,branch=branch,period=f'{start} to {end}',profit=str(profit),retained_earnings=retained.code)
    return doc,profit


@login_required
def year_end(request):
    if not permissions.can(request.user,'accounting'):raise PermissionDenied('You do not have access to this page.')
    places=access.branches(request.user)
    retained_default=Account.objects.filter(code='COA-3900').first()
    equity=Account.objects.filter(root_type='Equity',is_group=False,enabled=True).order_by('code')
    ctx=dict(title='Year-end close',shops=places,equity=equity,today=timezone.localdate(),selected=None,result=None,retained_default=retained_default)
    params=request.POST if request.method=='POST' else request.GET
    if params.get('branch') and params.get('from_date') and params.get('to_date'):
        try:
            branch=get_object_or_404(places,pk=params['branch']);start=date.fromisoformat(params['from_date']);end=date.fromisoformat(params['to_date'])
            ctx.update(selected=branch,start=start,end=end,result=preview(branch,start,end))
            retained=equity.filter(pk=params['retained']).first() if params.get('retained') else retained_default
            ctx['retained']=retained
            if request.method=='POST':
                if not retained:raise ValidationError('Choose the retained earnings account.')
                doc,profit=close_year(request.user,branch,start,end,retained)
                messages.success(request,f'{branch.name}: period closed. {"Profit" if profit>=0 else "Loss"} of PKR {abs(profit):,.2f} moved to {retained.name}; everything through {end:%d %b %Y} is now locked.')
                return redirect('detail',pk=doc.pk)
        except ValueError:messages.error(request,'Enter valid dates.')
        except ValidationError as error:messages.error(request,'; '.join(error.messages))
    return render(request,'yearend.html',ctx)
