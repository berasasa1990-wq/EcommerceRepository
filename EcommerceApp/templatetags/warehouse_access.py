from django import template
from EcommerceApp.warehouse_access import can_access_route, is_subscription_owner

register = template.Library()
register.filter('warehouse_can', can_access_route)
register.filter('subscription_owner', is_subscription_owner)
