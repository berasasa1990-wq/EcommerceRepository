from django.conf import settings
from django.core.management.base import BaseCommand

from EcommerceApp.emails import (
    EmailNotConfiguredError,
    send_admin_order_notification,
    send_customer_order_confirmation,
)
from EcommerceApp.models import Order


class Command(BaseCommand):
    help = 'Testira slanje email obavijesti za narudžbu (admin + opcionalno kupac).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--broj',
            help='Broj narudžbe (npr. 202606290002). Bez argumenata koristi zadnju narudžbu.',
        )
        parser.add_argument(
            '--admin-only',
            action='store_true',
            help='Pošalji samo obavijest trgovini.',
        )

    def handle(self, *args, **options):
        self.stdout.write(f'SMTP: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}')
        self.stdout.write(f'Korisnik: {settings.EMAIL_HOST_USER}')
        self.stdout.write(f'Obavijesti na: {settings.ORDER_NOTIFICATION_EMAIL}')
        self.stdout.write(f'Lozinka postavljena: {"da" if settings.EMAIL_HOST_PASSWORD else "ne"}')

        broj = options.get('broj')
        if broj:
            order = Order.objects.filter(broj=broj).first()
        else:
            order = Order.objects.order_by('-id').first()

        if not order:
            self.stderr.write(self.style.ERROR('Nema narudžbi za test.'))
            return

        self.stdout.write(f'Test narudžba: #{order.broj} ({order.email})')

        try:
            send_admin_order_notification(order)
            self.stdout.write(self.style.SUCCESS(
                f'Admin email poslan na {settings.ORDER_NOTIFICATION_EMAIL}',
            ))
            if not options['admin_only']:
                send_customer_order_confirmation(order)
                self.stdout.write(self.style.SUCCESS(
                    f'Kupac email poslan na {order.email}',
                ))
        except EmailNotConfiguredError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Greška: {exc}'))
            raise