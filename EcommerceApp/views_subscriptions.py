from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms_subscriptions import PlanFeaturesForm, SubscriptionForm
from .models import MagacinPlan, MagacinSubscription, MagacinSubscriptionRequest
from .warehouse_access import FEATURES, allowed_features, is_subscription_owner, subscription_for


def plan_cards():
    return [{'plan': plan, 'labels': [FEATURES[key] for key in (FEATURES if plan.code == 'ultimate' else plan.features) if key in FEATURES]}
            for plan in MagacinPlan.objects.order_by('id')]


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def plans(request):
    if request.method == 'POST':
        plan = get_object_or_404(MagacinPlan, code=request.POST.get('plan'))
        # A request never grants access or changes an existing paid entitlement.
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            MagacinSubscriptionRequest.objects.update_or_create(
                user=request.user, status='pending', defaults={'plan': plan})
        messages.success(request, 'Zahtjev je poslan. Pristup počinje nakon aktivacije vlasnika magacina.')
        return redirect('magacin_planovi')
    return render(request, 'account/magacin_plans.html', {
        'plan_cards': plan_cards(), 'subscription': subscription_for(request.user),
        'has_warehouse_access': bool(allowed_features(request.user)),
        'pending_request': MagacinSubscriptionRequest.objects.filter(user=request.user, status='pending').select_related('plan').first(),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def subscriptions(request):
    if not is_subscription_owner(request.user):
        raise PermissionDenied
    form = SubscriptionForm()
    edit_plan = None
    plan_form = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'assign':
            form = SubscriptionForm(request.POST)
            if form.is_valid():
                MagacinSubscription.objects.update_or_create(user=form.target_user, defaults={
                    'plan': form.cleaned_data['plan'], 'active': form.cleaned_data['active'],
                    'expires_on': form.cleaned_data['expires_on'], 'assigned_by': request.user,
                })
                messages.success(request, 'Pretplata je sačuvana.')
                return redirect('staff_magacin_pretplate')
        elif action == 'features':
            edit_plan = get_object_or_404(MagacinPlan, code=request.POST.get('plan'))
            plan_form = PlanFeaturesForm(request.POST)
            if plan_form.is_valid():
                edit_plan.features = list(FEATURES) if edit_plan.code == 'ultimate' else plan_form.cleaned_data['features']
                edit_plan.save(update_fields=['features'])
                messages.success(request, 'Pristupi plana su sačuvani.')
                return redirect('staff_magacin_pretplate')
        elif action in ('approve', 'reject'):
            with transaction.atomic():
                pending = get_object_or_404(MagacinSubscriptionRequest.objects.select_for_update(), pk=request.POST.get('request_id'), status='pending')
                if action == 'approve':
                    if not pending.user.is_active or is_subscription_owner(pending.user):
                        raise PermissionDenied
                    MagacinSubscription.objects.update_or_create(user=pending.user, defaults={
                        'plan': pending.plan, 'active': True, 'expires_on': None, 'assigned_by': request.user,
                    })
                pending.status = 'approved' if action == 'approve' else 'rejected'
                pending.reviewed_by = request.user
                pending.reviewed_at = timezone.now()
                pending.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            messages.success(request, 'Zahtjev je odobren.' if action == 'approve' else 'Zahtjev je odbijen.')
            return redirect('staff_magacin_pretplate')
        elif action == 'deactivate':
            sub = get_object_or_404(MagacinSubscription, pk=request.POST.get('subscription_id'))
            if is_subscription_owner(sub.user):
                raise PermissionDenied
            sub.active = False
            sub.assigned_by = request.user
            sub.save(update_fields=['active', 'assigned_by', 'updated_at'])
            messages.success(request, 'Pretplata je deaktivirana.')
            return redirect('staff_magacin_pretplate')
        else:
            raise PermissionDenied
    elif request.GET.get('edit'):
        sub = get_object_or_404(MagacinSubscription.objects.select_related('user'), pk=request.GET['edit'])
        form = SubscriptionForm(initial={'email': sub.user.email, 'plan': sub.plan_id, 'active': sub.active, 'expires_on': sub.expires_on})
    cards = plan_cards()
    for card in cards:
        card['form'] = plan_form if card['plan'] == edit_plan else PlanFeaturesForm(initial={'features': card['plan'].features}, auto_id=f"id_{card['plan'].code}_%s")
    from .views_magacin import _magacin_context
    context = _magacin_context(request, section='pretplate', page_title='Pretplate')
    context.update({
        'plan_cards': cards, 'form': form,
        'pending_requests': MagacinSubscriptionRequest.objects.filter(status='pending').select_related('user', 'plan'),
        'subscriptions': Paginator(MagacinSubscription.objects.select_related('user', 'plan').order_by('-updated_at'), 30).get_page(request.GET.get('page')),
        'today': timezone.localdate(),
    })
    return render(request, 'staff/magacin/pretplate.html', context)
