from types import SimpleNamespace

from django.template import Context, TemplateDoesNotExist
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import SimpleTestCase
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from . import views


class LiveCenterRemovalTests(SimpleTestCase):
    def test_removed_routes_are_unavailable(self):
        for path in ['/nalog/uzivo-analitika/', '/nalog/uzivo-analitika/podaci/']:
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
        for name in ['staff_live_analytics', 'staff_live_analytics_data']:
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name)
            self.assertFalse(hasattr(views, name))
        self.assertFalse(hasattr(views, '_live_analytics_context'))
        with self.assertRaises(TemplateDoesNotExist):
            get_template('staff/live_analytics.html')

    def test_admin_menu_renders_with_remaining_sections(self):
        template = get_template('staff/admin_panel.html').template
        content = next(block for block in template.nodelist.get_nodes_by_type(BlockNode)
                       if block.name == 'content')
        html = content.render(Context({'user': SimpleNamespace(is_superuser=True)}))
        self.assertNotIn('Live centar', html)
        self.assertNotIn('uzivo-analitika', html)
        for name in ['staff_hero_settings', 'staff_magacin', 'staff_orders_validation',
                     'staff_scratch_analytics', 'staff_loyalty_system']:
            self.assertIn(reverse(name), html)

    def test_shared_visitor_and_cart_endpoints_still_resolve(self):
        for name in ['live_visitor_heartbeat', 'live_visitor_leave', 'public_online_visitors',
                     'staff_active_carts', 'staff_b2b_live']:
            with self.subTest(name=name):
                self.assertEqual(resolve(reverse(name)).url_name, name)
