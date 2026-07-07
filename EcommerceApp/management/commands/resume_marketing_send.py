from django.core.management.base import BaseCommand

from EcommerceApp.emails import send_marketing_campaign_batch
from EcommerceApp.models import MarketingEmailCampaign


class Command(BaseCommand):
    help = 'Nastavi batch slanje marketing kampanje koja je ostala u statusu "sending".'

    def add_arguments(self, parser):
        parser.add_argument('kampanja_id', type=int, nargs='?', default=None)

    def handle(self, *args, **options):
        kampanja_id = options['kampanja_id']
        if kampanja_id:
            campaigns = MarketingEmailCampaign.objects.filter(
                pk=kampanja_id,
                status=MarketingEmailCampaign.Status.SENDING,
            )
        else:
            campaigns = MarketingEmailCampaign.objects.filter(
                status=MarketingEmailCampaign.Status.SENDING,
            ).order_by('kreirano')

        if not campaigns.exists():
            self.stdout.write(self.style.WARNING('Nema kampanja u statusu slanja.'))
            return

        for campaign in campaigns:
            self.stdout.write(
                f'Nastavljam kampanju #{campaign.pk} ({campaign.naslov})…',
            )
            while campaign.status == MarketingEmailCampaign.Status.SENDING:
                result = send_marketing_campaign_batch(campaign)
                campaign.refresh_from_db()
                self.stdout.write(
                    f'  {result["offset"]}/{result["total"]} · '
                    f'{result["sent"]} poslano · {result["failed"]} grešaka',
                )
                if result['done']:
                    break
            self.stdout.write(self.style.SUCCESS(f'Završeno: kampanja #{campaign.pk}'))