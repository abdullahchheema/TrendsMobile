from django import template
from ..words import amount_in_words

register=template.Library()


@register.filter
def words(value):
    """{{ doc.total|words }} -> 'Twenty Five Lakh Rupees Only'"""
    return amount_in_words(value)


@register.filter
def mul(value,other):
    """{{ line.rate|mul:line.quantity }}"""
    try:return value*other
    except TypeError:return ''
