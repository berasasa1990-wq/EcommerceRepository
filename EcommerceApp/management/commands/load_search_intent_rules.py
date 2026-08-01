"""Seed osnovnih SearchIntentRule zapisa (bez M2M veza — te ručno u adminu)."""

from django.core.management.base import BaseCommand

from EcommerceApp.search.intent import seed_default_intent_rules


class Command(BaseCommand):
    help = (
        'Kreira osnovna intent pravila (som, početnički feeder, štap za Savu). '
        'Kategorije/proizvode poveži ručno u adminu.'
    )

    def handle(self, *args, **options):
        stats = seed_default_intent_rules()
        self.stdout.write(self.style.SUCCESS(
            f"Intent pravila: +{stats['created']} (ukupno {stats['total']}). "
            'Poveži kategorije/tagove/proizvode u adminu.',
        ))
