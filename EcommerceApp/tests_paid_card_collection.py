from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase


class PaidCardCollectionTests(SimpleTestCase):
    def test_collection_display_preserves_total_for_unpaid_orders(self):
        root = Path(settings.BASE_DIR) / 'EcommerceApp/template/bera_reference'
        for paid in (True, False):
            source = SimpleNamespace(placeno_karticom=lambda: paid)
            order = SimpleNamespace(source_order=source)
            context = {
                'order': order, 'order_total': 125,
                'job': {'order': order, 'total': 125},
                'summary': {'ukupno': 125}, 'paid_by_card': paid,
            }
            for name in ('staff/wms/order_detail.html', 'staff/wms/orders_print.html', 'emails/order_admin.html'):
                with self.subTest(paid=paid, template=name):
                    # Render the actual collection row without unrelated shell context.
                    text = (root / name).read_text().split('Ukupno za naplatu', 1)[1]
                    text = text.split(' KM', 1)[0] + ' KM'
                    html = Template(text).render(Context(context, use_l10n=False))
                    self.assertIn('0.00 KM' if paid else '125.00 KM', html)
