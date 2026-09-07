from django import template
from EcommerceApp.warehouse_access import can_access_route

register = template.Library()
register.filter('warehouse_can', can_access_route)
