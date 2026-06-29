from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from EcommerceApp.models import Order
from EcommerceApp.render_sync import sync_je_aktivan, sync_korisnik, sync_narudzba


class Command(BaseCommand):
    help = 'Prepiše sve korisnike, loyalty kartice i narudžbe na Render produkciju.'

    def handle(self, *args, **options):
        if not sync_je_aktivan():
            self.stderr.write(
                'Sync nije aktivan. Postavite SYNC_REMOTE_URL i SYNC_API_KEY u .env.',
            )
            return

        users = User.objects.filter(email__isnull=False).exclude(email='').order_by('id')
        orders = Order.objects.prefetch_related('stavke').select_related('korisnik').order_by('kreirana')

        self.stdout.write(f'Sync korisnika: {users.count()}')
        for user in users:
            result = sync_korisnik(user)
            status = 'OK' if result and result.get('ok') else 'GREŠKA'
            self.stdout.write(f'  [{status}] {user.email}')

        self.stdout.write(f'Sync narudžbi: {orders.count()}')
        for order in orders:
            result = sync_narudzba(order)
            status = 'OK' if result and result.get('ok') else 'GREŠKA'
            self.stdout.write(f'  [{status}] #{order.broj}')

        self.stdout.write(self.style.SUCCESS('Sync završen.'))