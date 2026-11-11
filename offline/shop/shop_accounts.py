"""Shop accounts: the super admin creates one login for each shop. That login is the whole "staff" model: whoever signs in to a
shop's account does the selling, buying and taking of money for that shop, and sees only that shop's records."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm,UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import redirect,render
from .models import Branch,OnlineStaff,ShopSettings,Staff,SupabaseIdentity
from . import shopdefaults,supabase


def _owner(user):
    if not user.is_superuser:raise PermissionDenied('Only the super admin can manage shop accounts.')


def create_login(branch,username,password):
    """Create the shop's login and link it to the shop. Returns the user. Raises ValidationError on any problem."""
    User=get_user_model()
    if Staff.objects.filter(branch=branch,user__isnull=False,enabled=True).exists():raise ValidationError('This shop already has a login. Reset its password instead.')
    validate_password(password)
    external=None
    try:
        with transaction.atomic():
            if settings.SUPABASE_AUTH_ENABLED:external=supabase.create_user(username,password)
            account=User(username=username[:150],is_active=True)
            if external:account.email=username;account.set_unusable_password()
            else:account.set_password(password)
            account.save()
            if external:SupabaseIdentity.objects.create(auth_id=external['id'],user=account)
            person=shopdefaults.shop_person(branch);person.user=account;person.enabled=True;person.save()
    except IntegrityError:
        if external:_forget(external['id'])
        raise ValidationError('That username is already taken. Choose another.')
    except (ValidationError,supabase.ProviderUnavailable) as error:
        if external:_forget(external['id'])
        raise ValidationError(error.messages if hasattr(error,'messages') else str(error))
    return account


def create_online_login(name,username,password):
    """The online-store login: runs online orders, online stock, products and photos and online refunds, and has no shop of its own."""
    User=get_user_model();validate_password(password);external=None
    try:
        with transaction.atomic():
            if settings.SUPABASE_AUTH_ENABLED:external=supabase.create_user(username,password,{'online':True})
            account=User(username=username[:150],is_active=True)
            if external:account.email=username;account.set_unusable_password()
            else:account.set_password(password)
            account.save()
            if external:SupabaseIdentity.objects.create(auth_id=external['id'],user=account)
            OnlineStaff.objects.create(user=account,name=name[:180],enabled=True)
    except IntegrityError:
        if external:_forget(external['id'])
        raise ValidationError('That username is already taken. Choose another.')
    except (ValidationError,supabase.ProviderUnavailable) as error:
        if external:_forget(external['id'])
        raise ValidationError(error.messages if hasattr(error,'messages') else str(error))
    return account


class OnlineCreateForm(forms.Form):
    def __init__(self,*a,**k):
        super().__init__(*a,**k)
        self.fields['online_name']=forms.CharField(label='Name of the person',max_length=180)
        self.fields['online_username']=forms.EmailField(label='Sign-in email') if settings.SUPABASE_AUTH_ENABLED else forms.CharField(label='Username',max_length=150)
        self.fields['online_password']=forms.CharField(label='Password',widget=forms.PasswordInput(attrs={'autocomplete':'new-password'}),min_length=8)


def _forget(uid):
    try:supabase.delete_user(uid)
    except (ValidationError,supabase.ProviderUnavailable):pass


def reset_password(account,password):
    validate_password(password,account)
    if settings.SUPABASE_AUTH_ENABLED:
        identity=SupabaseIdentity.objects.filter(user=account).first()
        if not identity:raise ValidationError('This account is not linked to the sign-in service.')
        try:supabase.update_password(identity.auth_id,password)
        except supabase.ProviderUnavailable as error:raise ValidationError(str(error))
    else:
        account.set_password(password);account.save(update_fields=['password'])


class CreateForm(forms.Form):
    def __init__(self,*a,**k):
        super().__init__(*a,**k)
        waiting=Branch.objects.filter(enabled=True).exclude(pk__in=Staff.objects.filter(user__isnull=False,enabled=True).values('branch_id')).order_by('name')
        self.fields['branch']=forms.ModelChoiceField(label='Shop',queryset=waiting,empty_label='Choose a shop without a login')
        self.fields['username']=forms.EmailField(label='Sign-in email') if settings.SUPABASE_AUTH_ENABLED else forms.CharField(label='Username',max_length=150)
        self.fields['password']=forms.CharField(label='Password',widget=forms.PasswordInput(attrs={'autocomplete':'new-password'}),min_length=8)


class ResetForm(forms.Form):
    def __init__(self,*a,**k):
        super().__init__(*a,**k)
        has=Branch.objects.filter(pk__in=Staff.objects.filter(user__isnull=False,enabled=True).values('branch_id')).order_by('name')
        self.fields['reset_branch']=forms.ModelChoiceField(label='Shop',queryset=has,empty_label='Choose a shop')
        self.fields['new_password']=forms.CharField(label='New password',widget=forms.PasswordInput(attrs={'autocomplete':'new-password'}),min_length=8)


class EditForm(forms.Form):
    def __init__(self,*a,**k):
        super().__init__(*a,**k)
        self.fields['edit_branch']=forms.ModelChoiceField(label='Shop',queryset=Branch.objects.order_by('name'),empty_label='Choose a shop')
        self.fields['name']=forms.CharField(label='Shop name',max_length=180)
        self.fields['shop_phone']=forms.CharField(label='Contact phone',max_length=180,required=False)
        self.fields['shop_address']=forms.CharField(label='Address',required=False,widget=forms.Textarea(attrs={'rows':2}))


def edit_shop(branch,name,phone,address):
    name=' '.join(name.split())
    if not name:raise ValidationError('Enter the shop name.')
    if Branch.objects.filter(name__iexact=name).exclude(pk=branch.pk).exists():raise ValidationError('Another shop already has this name.')
    with transaction.atomic():
        branch.name=name;branch.save(update_fields=['name','modified'])
        config,_=ShopSettings.objects.get_or_create(branch=branch);config.shop_phone=phone;config.shop_address=address;config.save()
        Staff.objects.filter(branch=branch,code=branch.code+'-SHOP').update(name=name)


@login_required
def accounts(request):
    _owner(request.user)
    online_form=OnlineCreateForm(request.POST if request.POST.get('action')=='online_create' else None)
    create=CreateForm(request.POST if request.POST.get('action')=='create' else None);reset=ResetForm(request.POST if request.POST.get('action')=='reset' else None);edit=EditForm(request.POST if request.POST.get('action')=='edit' else None)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='create' and create.is_valid():
                d=create.cleaned_data;create_login(d['branch'],d['username'],d['password'])
                messages.success(request,f'Login created for {d["branch"].name}. Give them the username and password; they will only see this shop.');return redirect('users')
            if action=='reset' and reset.is_valid():
                d=reset.cleaned_data;person=Staff.objects.filter(branch=d['reset_branch'],user__isnull=False,enabled=True).select_related('user').first()
                reset_password(person.user,d['new_password']);messages.success(request,f'Password changed for {d["reset_branch"].name}.');return redirect('users')
            if action=='edit' and edit.is_valid():
                d=edit.cleaned_data;edit_shop(d['edit_branch'],d['name'],d['shop_phone'],d['shop_address']);messages.success(request,'Shop details saved.');return redirect('users')
            if action=='online_create' and online_form.is_valid():
                d=online_form.cleaned_data;create_online_login(d['online_name'],d['online_username'],d['online_password'])
                messages.success(request,'Online-store login created. It can only open the online store screens.');return redirect('users')
            if action=='online_toggle':
                who=OnlineStaff.objects.select_related('user').get(pk=request.POST['online'])
                who.enabled=not who.enabled;who.save(update_fields=['enabled']);who.user.is_active=who.enabled;who.user.save(update_fields=['is_active'])
                messages.success(request,f'{who.name} {"enabled" if who.enabled else "disabled"}.');return redirect('users')
            if action=='online_reset':
                who=OnlineStaff.objects.select_related('user').get(pk=request.POST['online']);reset_password(who.user,request.POST.get('new_password',''))
                messages.success(request,f'Password changed for {who.name}.');return redirect('users')
            if action=='toggle':
                person=Staff.objects.select_related('user').get(pk=request.POST['person'],user__isnull=False)
                person.user.is_active=not person.user.is_active;person.user.save(update_fields=['is_active'])
                messages.success(request,f'{person.branch.name} login {"enabled" if person.user.is_active else "disabled"}.');return redirect('users')
        except ValidationError as error:
            (online_form if action=='online_create' else create if action=='create' else edit if action=='edit' else reset).add_error(None,'; '.join(error.messages))
    rows=[]
    for branch in Branch.objects.filter(enabled=True).order_by('name'):
        person=Staff.objects.filter(branch=branch,user__isnull=False,enabled=True).select_related('user').first()
        rows.append(dict(branch=branch,person=person,login=(person.user.email or person.user.get_username()) if person else '',active=person.user.is_active if person else None))
    return render(request,'shop_accounts.html',dict(title='Shop accounts',rows=rows,create=create,reset=reset,edit=edit,online_form=online_form,online_rows=OnlineStaff.objects.select_related('user').order_by('name')))
