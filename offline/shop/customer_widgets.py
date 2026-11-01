from django import forms
from .models import Party


class CustomerNameInput(forms.TextInput):
    template_name='shop/customer_name_input.html'

    def get_context(self,name,value,attrs):
        context=super().get_context(name,value,attrs)
        context['widget']['attrs']['list']=(context['widget']['attrs'].get('id') or name)+'-suggestions'
        context['customer_names']=Party.objects.filter(kind='Customer',enabled=True).order_by('name').values_list('name',flat=True)
        return context
