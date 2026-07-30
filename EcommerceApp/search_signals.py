"""Signali za održavanje product search indeksa."""
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .models import Brand, Category, Product, ProductVariation


def _reindex_product(product_id):
    if not product_id:
        return
    try:
        from .product_search import update_product_search_index
        update_product_search_index(product_id)
    except Exception:
        pass


@receiver(post_save, sender=Product)
def product_search_on_save(sender, instance, **kwargs):
    _reindex_product(instance.pk)


@receiver(post_save, sender=ProductVariation)
@receiver(post_delete, sender=ProductVariation)
def variation_search_on_change(sender, instance, **kwargs):
    _reindex_product(getattr(instance, 'artikal_id', None))


@receiver(post_save, sender=Brand)
def brand_search_on_save(sender, instance, **kwargs):
    for pk in Product.objects.filter(brend_id=instance.pk).values_list('pk', flat=True).iterator():
        _reindex_product(pk)


@receiver(post_save, sender=Category)
def category_search_on_save(sender, instance, **kwargs):
    """Kad se promijene naziv/tagovi kategorije — reindex artikala."""
    ids = set(
        Product.objects.filter(kategorija_id=instance.pk).values_list('pk', flat=True)
    )
    child_ids = list(instance.podkategorije.values_list('pk', flat=True))
    if child_ids:
        ids.update(
            Product.objects.filter(kategorija_id__in=child_ids).values_list('pk', flat=True)
        )
    for pk in ids:
        _reindex_product(pk)


def _product_tags_m2m(sender, instance, **kwargs):
    _reindex_product(getattr(instance, 'pk', None))


# through model resolved after models load
try:
    m2m_changed.connect(_product_tags_m2m, sender=Product.tagovi.through)
except Exception:
    pass
