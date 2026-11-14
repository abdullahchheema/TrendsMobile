"""Independent calculation oracle for the QA audit (see qa-audit/CALCULATION_VERIFICATION.md).

This file deliberately imports NOTHING from the application. Every figure is worked out again from first principles with exact
fractions and whole paisa, so a test that compares the application with this oracle is comparing two separate implementations,
not the application with itself.

Rounding policy used (each one is the policy found in the application's own code and screens, written down here once):
  * shop invoices (line amounts, percentage discount, sales tax, commission): round half to even, to the paisa
    (services.money and static/pricing.js both do this);
  * the website's percentage-off price, depreciation, prepaid release and loan interest: round half up, to the paisa
    (online_orders._after_discount, the web_* SQL views and advanced.cents).
The two rules differ only on an exact half paisa. That inconsistency is reported as a business decision, not hidden here."""
from datetime import date,timedelta
from fractions import Fraction
import calendar

HALF_EVEN='half_even'
HALF_UP='half_up'


def F(value):
    """An exact fraction from a string, int or Decimal (never from a float)."""
    if isinstance(value,float):raise TypeError('Money must never pass through a float.')
    return Fraction(str(value))


def to_paisa(value,mode=HALF_EVEN):
    """Round an exact amount in rupees to whole paisa."""
    scaled=F(value)*100 if not isinstance(value,Fraction) else value*100
    whole=scaled.numerator//scaled.denominator;rest=scaled-whole
    if rest>Fraction(1,2) or (rest==Fraction(1,2) and (mode==HALF_UP or whole%2==1)):whole+=1
    return whole


def rupees(paisa):
    """'1234.50' from 123450 paisa (text, so nothing is lost)."""
    sign='-' if paisa<0 else '';paisa=abs(paisa)
    return f'{sign}{paisa//100}.{paisa%100:02d}'


def largest_remainder(amount,weights):
    """Share `amount` paisa over the weights so the parts add up exactly: each part gets its floor, the paisa left over go to the
    largest fractional parts, earlier lines first on a tie."""
    total=sum(weights)
    if total==0:
        if amount:raise ValueError('Nothing to share the amount over.')
        return [0 for _ in weights]
    quotas=[Fraction(amount*w,total) for w in weights]
    parts=[q.numerator//q.denominator for q in quotas]
    order=sorted(range(len(weights)),key=lambda i:(-(quotas[i]-parts[i]),i))
    for i in order[:amount-sum(parts)]:parts[i]+=1
    return parts


def invoice(lines,discount_type='amount',discount_value='0',tax_rate='0'):
    """lines: [(quantity, unit price)]. Discount is before tax; tax is added on the discounted amount (tax-exclusive prices).
    Returns every figure in paisa: per line (base, discount, net, tax, total) and the invoice totals."""
    bases=[to_paisa(F(q)*F(r)) for q,r in lines]
    subtotal=sum(bases)
    discount=to_paisa(Fraction(subtotal,100)*F(discount_value)/100) if discount_type=='percent' else to_paisa(F(discount_value))
    if discount>subtotal:raise ValueError('The discount is more than the items.')
    net=subtotal-discount
    tax=to_paisa(Fraction(net,100)*F(tax_rate)/100)
    line_discounts=largest_remainder(discount,bases)
    line_nets=[b-d for b,d in zip(bases,line_discounts)]
    line_taxes=largest_remainder(tax,line_nets)
    rows=[dict(base=b,discount=d,net=n,tax=t,total=n+t) for b,d,n,t in zip(bases,line_discounts,line_nets,line_taxes)]
    return dict(lines=rows,subtotal=subtotal,discount=discount,net=net,tax=tax,total=net+tax)


def settle(total,payments):
    """Invoice total = payments received + remaining receivable. `payments` in rupees; returns (received, remaining) in paisa."""
    received=sum(to_paisa(F(p)) for p in payments)
    return received,total-received


def commission(sales,rates):
    """sales: [(date, net amount in rupees, date of the original sale for a return or None)]; rates: [(effective from, percent)].
    A sale earns the rate in force on its own date; a return gives back what the original sale earned, at that sale's rate."""
    ordered=sorted(rates)
    def rate_on(day):
        found=ordered[0][1]
        for start,rate in ordered:
            if start<=day:found=rate
        return found
    return sum(to_paisa(F(amount)*F(rate_on(original or day))/100) for day,amount,original in sales)


def online_price(base,percent_off):
    """What the website charges: the base price less the percentage off, half up to the paisa."""
    return to_paisa(F(base)*(100-F(percent_off or 0))/100,HALF_UP)


def delivery_fee(subtotal,default_fee,free_from=None,province='',rules=None):
    """Free from the free-delivery amount; otherwise the province's own fee; otherwise the default fee. All in rupees (text)."""
    if free_from is not None and F(subtotal)>=F(free_from):return 0
    key=' '.join(str(province or '').split()).casefold()
    for name,fee in (rules or {}).items():
        if name.casefold()==key and key:return to_paisa(F(fee))
    return to_paisa(F(default_fee))


def online_order(items,delivery,discount='0'):
    """items: [(unit price, quantity, unit cost)]. Returns paisa: subtotal, total, cost (what the shops are owed), profit on the goods."""
    subtotal=sum(to_paisa(F(p))*q for p,q,_ in items)
    cost=sum(to_paisa(F(c))*q for _,q,c in items)
    less=to_paisa(F(discount))
    return dict(subtotal=subtotal,total=subtotal-less+delivery,shop_payable=cost,goods_revenue=subtotal-less,goods_profit=subtotal-less-cost,delivery=delivery)


def weighted_average(movements):
    """Accessory stock at moving average cost. movements: [('in', qty, unit cost) | ('out', qty)]. Returns (quantity, value in paisa,
    [cost in paisa of each 'out']). The last units out take whatever value is left, so an empty shelf is worth nothing."""
    qty=Fraction(0);value=0;outs=[]
    for move in movements:
        if move[0]=='in':
            qty+=F(move[1]);value+=to_paisa(F(move[1])*F(move[2]))
        else:
            take=F(move[1])
            if take>qty:raise ValueError('More stock out than on the shelf.')
            unit=to_paisa(Fraction(value,100)/qty)                 # the average cost per unit, to the paisa
            out=value if take==qty else to_paisa(Fraction(unit,100)*take)
            outs.append(out);qty-=take;value-=out
    return qty,value,outs


def month_end(day):return day.replace(day=calendar.monthrange(day.year,day.month)[1])


def months_to(start,through):
    """Whole months counted from the month of `start` up to the last month end on or before `through` (0 if none)."""
    end=through if through==month_end(through) else through.replace(day=1)-timedelta(days=1)
    if end<month_end(start):return 0
    return (end.year-start.year)*12+end.month-start.month+1


def straight_line(cost,salvage,life_months,purchased,through):
    """Accumulated depreciation in paisa: (cost - scrap value) spread evenly over the life, the month of purchase counted as the
    first month, never more than the depreciable amount."""
    base=to_paisa(F(cost))-to_paisa(F(salvage));months=months_to(purchased,through)
    return min(base,to_paisa(Fraction(base,100)*months/life_months,HALF_UP))


def reducing_balance(cost,salvage,yearly_rate,purchased,through):
    """Accumulated depreciation in paisa: each month takes rate/12 of the book value at the start of that month, never below scrap."""
    book=to_paisa(F(cost));floor=to_paisa(F(salvage));done=0
    for _ in range(months_to(purchased,through)):
        if book<=floor:break
        step=min(to_paisa(Fraction(book,100)*F(yearly_rate)/1200,HALF_UP),book-floor);done+=step;book-=step
    return done


def prepaid_released(amount,months,start,through):
    total=to_paisa(F(amount));used=months_to(start,through)
    return min(total,to_paisa(Fraction(total,100)*used/months,HALF_UP))


def loan_interest(outstanding,yearly_rate):
    return to_paisa(F(outstanding)*F(yearly_rate)/1200,HALF_UP)


def instalments(amount,count):
    paisa=to_paisa(F(amount));base,extra=divmod(paisa,count)
    return [base+(1 if i<extra else 0) for i in range(count)]


def trial_balance(rows):
    """rows: [(account, debit paisa, credit paisa)]. Returns {account: net debit paisa}, total debits, total credits."""
    nets={};debits=credits=0
    for account,debit,credit in rows:
        nets[account]=nets.get(account,0)+debit-credit;debits+=debit;credits+=credit
    return nets,debits,credits


def profit_and_loss(nets,sections):
    """nets from trial_balance; sections: {account: 'Income'|'Expense'|...}. Returns (income, expenses, profit) in paisa."""
    income=-sum(v for a,v in nets.items() if sections.get(a)=='Income')
    expenses=sum(v for a,v in nets.items() if sections.get(a)=='Expense')
    return income,expenses,income-expenses
