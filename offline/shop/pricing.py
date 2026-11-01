"""Exclusive sales tax, pre-tax invoice discounts and frozen per-line allocation."""
from decimal import Decimal,ROUND_FLOOR
import hashlib,json
from django.core.exceptions import ValidationError
from .models import SalesTaxRule,Account


def tax_version(rule):
    data=[str(rule.pk),str(rule.branch_id),rule.name,format(rule.rate,'.4f'),str(rule.account_id),rule.reference,rule.enabled]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False).encode()).hexdigest()


def rules(branch):
    return [dict(id=str(rule.pk),name=rule.name,rate=format(rule.rate,'.4f'),version=tax_version(rule))
        for rule in SalesTaxRule.objects.filter(branch=branch,enabled=True,account__enabled=True,account__is_group=False,account__account_type__in=['Tax','Current Liability']).select_related('account')]


def tax_rule(branch,pk):
    if not pk:return None
    try:rule=SalesTaxRule.objects.select_related('account').get(pk=pk,branch=branch,enabled=True)
    except (ValueError,TypeError,ValidationError,SalesTaxRule.DoesNotExist):raise ValidationError('Select an active sales-tax rule for this shop.')
    rule.full_clean()
    return rule


def inputs(doc,kind='amount',value='0',rule_id='',version='',capture=False):
    rule=tax_rule(doc.branch,rule_id)
    doc.data['adjustments']=dict(discount_type=kind or 'amount',discount_value=str(value or '0'),tax_rule=str(rule.pk) if rule else '',
        tax_version=tax_version(rule) if rule and capture else version or '')


def distribute(amount,weights):
    """Largest remainder in paisa, with input order as the stable tie break."""
    from . import services as s
    total=sum(weights,s.ZERO)
    if not total:
        if amount:raise ValidationError('Cannot allocate an amount across zero-value lines.')
        return [s.ZERO for _ in weights]
    cents=int(amount*100)
    shares=[Decimal(cents)*weight/total for weight in weights]
    parts=[int(share.to_integral_value(rounding=ROUND_FLOOR)) for share in shares]
    order=sorted(range(len(parts)),key=lambda i:(-(shares[i]-parts[i]),i))
    for index in order[:cents-sum(parts)]:parts[index]+=1
    return [Decimal(part)/100 for part in parts]


def calculate(doc,check_version=True):
    from . import services as s
    spec=doc.data.get('adjustments',{})
    if not isinstance(spec,dict):raise ValidationError('Invalid invoice adjustment values.')
    kind=spec.get('discount_type','amount') or 'amount'
    if kind not in ('amount','percent'):raise ValidationError('Choose an amount or percentage discount.')
    value=s.payment_money(spec.get('discount_value','0'))
    if value<0 or (kind=='percent' and value>100):raise ValidationError('Discount must be nonnegative; percentage cannot exceed 100.')
    lines=list(doc.lines.select_related('product').order_by('pk'))
    bases=[]
    for line in lines:
        if line.rate<0:raise ValidationError('Selling rates cannot be negative.')
        bases.append(s.money(s.quantity(line.quantity)*line.rate))
    subtotal=s.money(sum(bases,s.ZERO))
    discount=s.money(subtotal*value/100) if kind=='percent' else value
    if discount>subtotal:raise ValidationError('Invoice discount exceeds the item subtotal.')
    rule=tax_rule(doc.branch,spec.get('tax_rule'))
    if rule and check_version and spec.get('tax_version')!=tax_version(rule):raise ValidationError('The sales-tax rule changed or its version is missing. Reload the rate and save the draft again.')
    net=s.money(subtotal-discount);rate=rule.rate if rule else Decimal(0);tax=s.money(net*rate/100);grand=s.money(net+tax)
    discounts=distribute(discount,bases);nets=[base-less for base,less in zip(bases,discounts)];taxes=distribute(tax,nets)
    allocation={line.pk:dict(version=1,base=str(base),discount=str(less),net=str(net_line),tax=str(tax_line),total=str(net_line+tax_line))
        for line,base,less,net_line,tax_line in zip(lines,bases,discounts,nets,taxes)}
    summary=dict(version=1,subtotal=str(subtotal),discount_type=kind,discount_value=str(value),discount=str(discount),net_total=str(net),tax_total=str(tax),grand_total=str(grand),
        tax_rule=str(rule.pk) if rule else '',tax_version=tax_version(rule) if rule else '',tax_name=rule.name if rule else '',tax_rate=format(rate,'.4f'),tax_account=str(rule.account_id) if rule else '',tax_reference=rule.reference if rule else '')
    return summary,allocation


def frozen_lines(original,lines):
    """Validate the captured basis rather than recalculate old rates or guesses."""
    from . import services as s
    summary=original.data.get('pricing')
    if not summary or summary.get('version')!=1:return False
    totals=dict(base=s.ZERO,discount=s.ZERO,net=s.ZERO,tax=s.ZERO,total=s.ZERO)
    for line in lines:
        item=line.snapshot.get('pricing',{})
        if item.get('version')!=1:raise ValidationError('Invoice pricing snapshots are incomplete. Review the original invoice.')
        for key in totals:
            amount=s.money(item[key])
            if amount<0:raise ValidationError('Invalid invoice pricing snapshot.')
            totals[key]+=amount
        if s.money(item['base'])-s.money(item['discount'])!=s.money(item['net']) or s.money(item['net'])+s.money(item['tax'])!=s.money(item['total']):raise ValidationError('Invoice line totals do not reconcile.')
    for key,target in [('base','subtotal'),('discount','discount'),('net','net_total'),('tax','tax_total'),('total','grand_total')]:
        if totals[key]!=s.money(summary[target]):raise ValidationError('Invoice pricing totals do not reconcile.')
    if totals['total']!=original.total:raise ValidationError('Invoice pricing does not match its posted total. A reviewed credit calculation is required.')
    return True


def return_parts(source,prior,qty):
    from . import services as s
    item=source.snapshot.get('pricing')
    if not item or item.get('version')!=1:
        base=s.money((prior+qty)*source.rate)-s.money(prior*source.rate)
        return dict(base=base,discount=s.ZERO,net=base,tax=s.ZERO,total=base)
    result={key:s.money(Decimal(item[key])*(prior+qty)/source.quantity)-s.money(Decimal(item[key])*prior/source.quantity) for key in ('net','discount','tax')}
    result['base']=result['net']+result['discount'];result['total']=result['net']+result['tax']
    return result


def tax_account(original):
    from . import services as s
    value=original.data.get('pricing',{}).get('tax_account')
    try:account=Account.objects.get(pk=value,enabled=True,is_group=False,account_type__in=['Tax','Current Liability'])
    except (Account.DoesNotExist,ValueError,ValidationError):raise ValidationError('The original output-tax account needs review before this credit can post.')
    return account


def net_amount(doc):
    from . import services as s
    return s.money(doc.data['pricing']['net_total']) if doc.data.get('pricing',{}).get('version')==1 else doc.total


def line_net(line):
    from . import services as s
    if line.document.is_return and 'return_net' in line.snapshot:return -s.money(line.snapshot['return_net'])
    if not line.document.is_return and line.snapshot.get('pricing',{}).get('version')==1:return s.money(line.snapshot['pricing']['net'])
    return s.money(line.quantity*line.rate)*(-1 if line.document.is_return else 1)
