from django.contrib import admin
from django.conf import settings
from .models import SiteModule


@admin.register(SiteModule)
class SiteModuleAdmin(admin.ModelAdmin):
    list_display = ['code', 'enabled', 'paid_until', 'updated_at']
    readonly_fields = ['code', 'revision', 'updated_at']
    fields = ['code', 'enabled', 'paid_until', 'revision', 'updated_at']

    def get_readonly_fields(self, request, obj=None):
        if 'loyalty' in settings.BERA_LICENSE_ENFORCED_MODULES:
            return self.fields
        return self.readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import LoyaltyCard, LoyaltyTier, LoyaltyRules, LoyaltyEntry
from bera_licensing.services import module_enabled, require_module


class LoyaltyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return module_enabled('loyalty') and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return module_enabled('loyalty') and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return module_enabled('loyalty') and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return module_enabled('loyalty') and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        require_module('loyalty')
        super().save_model(request, obj, form, change)


@admin.register(LoyaltyRules)
class LoyaltyRulesAdmin(LoyaltyAdmin):
    def has_add_permission(self, request):
        return not LoyaltyRules.objects.exists() and super().has_add_permission(request)


@admin.register(LoyaltyTier)
class LoyaltyTierAdmin(LoyaltyAdmin):
    list_display = ['name', 'minimum', 'maximum', 'percent']


@admin.register(LoyaltyCard)
class LoyaltyCardAdmin(LoyaltyAdmin):
    list_display = ['customer', 'number', 'points']
    readonly_fields = ['customer', 'number', 'points', 'created_at']
    def has_add_permission(self, request):
        return False


@admin.register(LoyaltyEntry)
class LoyaltyEntryAdmin(LoyaltyAdmin):
    list_display = ['card', 'order', 'kind', 'points', 'created_at']
    readonly_fields = ['card', 'order', 'kind', 'points', 'created_at']
    def has_add_permission(self, request):
        return False
