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
from .models import Branch,Staff,SupabaseIdentity
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


@login_required
def accounts(request):
    _owner(request.user)
    create=CreateForm(request.POST if request.POST.get('action')=='create' else None);reset=ResetForm(request.POST if request.POST.get('action')=='reset' else None)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='create' and create.is_valid():
                d=create.cleaned_data;create_login(d['branch'],d['username'],d['password'])
                messages.success(request,f'Login created for {d["branch"].name}. Give them the username and password; they will only see this shop.');return redirect('users')
            if action=='reset' and reset.is_valid():
                d=reset.cleaned_data;person=Staff.objects.filter(branch=d['reset_branch'],user__isnull=False,enabled=True).select_related('user').first()
                reset_password(person.user,d['new_password']);messages.success(request,f'Password changed for {d["reset_branch"].name}.');return redirect('users')
            if action=='toggle':
                person=Staff.objects.select_related('user').get(pk=request.POST['person'],user__isnull=False)
                person.user.is_active=not person.user.is_active;person.user.save(update_fields=['is_active'])
                messages.success(request,f'{person.branch.name} login {"enabled" if person.user.is_active else "disabled"}.');return redirect('users')
        except ValidationError as error:
            (create if action=='create' else reset).add_error(None,'; '.join(error.messages))
    rows=[]
    for branch in Branch.objects.filter(enabled=True).order_by('name'):
        person=Staff.objects.filter(branch=branch,user__isnull=False,enabled=True).select_related('user').first()
        rows.append(dict(branch=branch,person=person,login=(person.user.email or person.user.get_username()) if person else '',active=person.user.is_active if person else None))
    return render(request,'shop_accounts.html',dict(title='Shop accounts',rows=rows,create=create,reset=reset))
