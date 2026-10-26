from django import template
from shop.seo import metadata
register = template.Library()


@register.simple_tag(takes_context=True)
def seo_metadata(context):
    return metadata(context)
