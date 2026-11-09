from django import forms
from .models import Party


class CustomerNameInput(forms.TextInput):
    template_name='shop/customer_name_input.html'
    branch=None          # set by the form that uses it; without a shop no names are suggested

    def get_context(self,name,value,attrs):
        context=super().get_context(name,value,attrs)
        context['widget']['attrs']['list']=(context['widget']['attrs'].get('id') or name)+'-suggestions'
        from . import access
        context['customer_names']=access.parties_for(self.branch,'Customer').filter(enabled=True).order_by('name').values_list('name',flat=True)
        return context


def name_input(branch,attrs=None):
    """A customer-name box that suggests only this shop's customers."""
    widget=CustomerNameInput(attrs=attrs or {});widget.branch=branch
    return widget
