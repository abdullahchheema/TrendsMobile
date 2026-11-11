"""Amounts in words, the way they are said and written on cheques in Pakistan: lakh, crore, arab."""
from decimal import Decimal,InvalidOperation

ONES=('','One','Two','Three','Four','Five','Six','Seven','Eight','Nine','Ten','Eleven','Twelve','Thirteen','Fourteen','Fifteen','Sixteen','Seventeen','Eighteen','Nineteen')
TENS=('','','Twenty','Thirty','Forty','Fifty','Sixty','Seventy','Eighty','Ninety')
SCALES=((10**9,'Arab'),(10**7,'Crore'),(10**5,'Lakh'),(1000,'Thousand'),(100,'Hundred'))


def _below_hundred(n):
    return ONES[n] if n<20 else (TENS[n//10]+(' '+ONES[n%10] if n%10 else ''))


def _whole(n):
    """Whole number in words (Pakistani grouping). 0 -> 'Zero'."""
    if n==0:return 'Zero'
    parts=[]
    for size,name in SCALES:
        if n>=size:
            count,n=divmod(n,size);parts.append(_whole(count)+' '+name)
    if n:parts.append(_below_hundred(n))
    return ' '.join(parts)


def amount_in_words(value,currency='Rupees'):
    """'Twenty Five Lakh Twenty Seven Thousand Rupees Only'; paisa are added when present. Returns '' for a blank or invalid amount."""
    try:amount=Decimal(str(value).replace(',','').strip())
    except (InvalidOperation,TypeError,ValueError,AttributeError):return ''
    if not amount.is_finite():return ''
    sign='Minus ' if amount<0 else ''
    cents=int((abs(amount)*100).to_integral_value())
    rupees,paisa=divmod(cents,100)
    text=f'{_whole(rupees)} {currency}'
    if paisa:text+=f' and {_whole(paisa)} Paisa'
    return f'{sign}{text} Only'
