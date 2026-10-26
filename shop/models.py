from decimal import Decimal
from .image_uploads import AVIFFileField
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Brand(models.Model):
    name = models.CharField('Naziv', max_length=150)
    logo = AVIFFileField('Logo', upload_to='brands/', blank=True)

    class Meta:
        verbose_name = 'Brend'
        verbose_name_plural = 'Brendovi'
        ordering = ['name']

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('brand', kwargs={'brand_pk': self.pk})

    def __str__(self):
        return self.name


class Category(models.Model):
    seo_title = models.CharField('SEO naslov', max_length=160, blank=True, help_text='Prazno koristi naziv. Preporuka oko 50–60 znakova.')
    seo_description = models.CharField('SEO opis', max_length=320, blank=True, help_text='Prazno koristi automatski opis. Preporuka oko 150–160 znakova.')

    name = models.CharField('Naziv', max_length=150)
    image = AVIFFileField('Slika', upload_to='categories/', blank=True)

    class Meta:
        verbose_name = 'Kategorija'
        verbose_name_plural = 'Kategorije'
        ordering = ['name']

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('category', kwargs={'category_pk': self.pk})

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def on_sale(self):
        from bera_licensing.services import module_enabled
        offers = models.Q(original_price__gt=models.F('price'))
        if module_enabled('bundle'):
            offers |= models.Q(sale_type='bundle', bundle_percent__gt=0, bundle_products__isnull=False)
        if module_enabled('quantity'):
            offers |= models.Q(sale_type='quantity', quantity_percent_2__isnull=False, quantity_percent_3__isnull=False, quantity_percent_over_3__isnull=False)
        if module_enabled('article_plus'):
            offers |= models.Q(sale_type='article_plus', plus_product__is_active=True, plus_percent__gt=0, plus_percent__lte=100) & ~models.Q(plus_product_id=models.F('pk')) & (models.Q(original_price__isnull=True) | models.Q(original_price__lte=models.F('price')))
        return self.filter(offers).distinct()

    def bestsellers(self):
        return self.annotate(
            order_count=models.Count(
                'order_items__order', distinct=True,
                filter=models.Q(order_items__order__status__in=['placed', 'shipped', 'completed']),
            )
        ).filter(order_count__gt=0).order_by('-order_count', '-created_at', '-pk')


class Product(models.Model):
    sale_ends_at = models.DateTimeField('Akcija traje do', null=True, blank=True, db_index=True, help_text='Opcionalno. Nakon ovog datuma i vremena akcija automatski prestaje.')

    seo_title = models.CharField('SEO naslov', max_length=160, blank=True, help_text='Prazno koristi naziv. Preporuka oko 50–60 znakova.')
    seo_description = models.CharField('SEO opis', max_length=320, blank=True, help_text='Prazno koristi automatski opis. Preporuka oko 150–160 znakova.')

    class SaleType(models.TextChoices):
        REGULAR = 'regular', 'Regularno sniženje'
        ARTICLE_PLUS = 'article_plus', 'Ponuda +'
        QUANTITY = 'quantity', 'Količinsko sniženje'
        BUNDLE = 'bundle', 'Bundle set'

    quantity_percent_2 = models.DecimalField('Popust za 2 komada (%)', max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])
    quantity_percent_3 = models.DecimalField('Popust za 3 komada (%)', max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])
    quantity_percent_over_3 = models.DecimalField('Popust za više od 3 komada (%)', max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])
    bundle_products = models.ManyToManyField('self', verbose_name='Dodatni artikli u setu', symmetrical=False, blank=True, related_name='bundle_parents')
    bundle_percent = models.DecimalField('Sniženje seta (%)', max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0.01), MaxValueValidator(100)])
    plus_product = models.ForeignKey('self', verbose_name='Drugi artikal', on_delete=models.SET_NULL, null=True, blank=True, related_name='plus_offers')
    plus_percent = models.DecimalField('Popust na drugi artikal (%)', max_digits=5, decimal_places=2, null=True, blank=True)
    sale_type = models.CharField('Vrsta akcije', max_length=20, choices=SaleType.choices, default=SaleType.REGULAR)
    objects = ProductQuerySet.as_manager()

    name = models.CharField('Naziv', max_length=255)
    description = models.TextField('Opis', blank=True)
    image = AVIFFileField('Slika', upload_to='products/', blank=True)
    price = models.DecimalField('Cijena', max_digits=10, decimal_places=2)
    original_price = models.DecimalField('Stara cijena', max_digits=10, decimal_places=2, null=True, blank=True)
    brand = models.ForeignKey(Brand, verbose_name='Brend', on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(Category, verbose_name='Kategorija', on_delete=models.SET_NULL, null=True, blank=True)
    stock = models.PositiveIntegerField('Količina na stanju', default=0)
    is_out_of_stock = models.BooleanField('Nije na stanju', default=False)
    is_active = models.BooleanField('Prikaži u trgovini', default=True)
    is_featured = models.BooleanField('Izdvojena ponuda', default=False)
    is_bestselling = models.BooleanField('Najprodavaniji', default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Artikal'
        verbose_name_plural = 'Artikli'
        ordering = ['-created_at', '-pk']

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('product_detail', kwargs={'pk': self.pk})

    def quantity_discount_percent(self, quantity):
        if quantity < 2 or not self.has_quantity_sale:
            return 0
        return (self.quantity_percent_2 if quantity == 2 else self.quantity_percent_3 if quantity == 3 else self.quantity_percent_over_3) or 0

    @property
    def has_quantity_sale(self):
        from bera_licensing.services import module_enabled
        return self.sale_type == 'quantity' and module_enabled('quantity')

    @property
    def has_bundle_sale(self):
        from bera_licensing.services import module_enabled
        return self.sale_type == 'bundle' and bool(self.bundle_percent) and module_enabled('bundle')

    @property
    def has_article_plus(self):
        from bera_licensing.services import module_enabled
        return (self.sale_type == 'article_plus' and module_enabled('article_plus') and self.plus_product_id
                and self.plus_product_id != self.pk and self.plus_product.is_active
                and self.plus_percent and 0 < self.plus_percent <= 100
                and not (self.original_price and self.original_price > self.price))

    @property
    def discount_percent(self):
        if self.original_price and self.original_price > self.price:
            from decimal import Decimal, ROUND_HALF_UP
            return int(((self.original_price - self.price) * Decimal(100) / self.original_price).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        return 0

    def __str__(self):
        return self.name


class StoreSettings(models.Model):
    free_delivery_threshold = models.DecimalField('Besplatna dostava od (KM)', max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], help_text='Ostavite prazno ako nema besplatne dostave prema iznosu.')
    delivery_price = models.DecimalField('Cijena dostave (KM)', max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], help_text='Unesite 0 za besplatnu dostavu.')
    logo = AVIFFileField('Logo trgovine', upload_to='store/', blank=True)

    def delivery_cost(self, subtotal):
        if self.free_delivery_threshold is not None and subtotal >= self.free_delivery_threshold:
            return 0
        return self.delivery_price

    class Meta:
        verbose_name = 'Podešavanje trgovine'
        verbose_name_plural = 'Podešavanje trgovine'


class Banner(models.Model):
    POSITION_CHOICES = [
        ('hero', 'Glavni banner'), ('offer_left', 'Lijeva ponuda'),
        ('offer_right', 'Desna ponuda'), ('middle', 'Veliki banner — ispod ponuda'), ('footer', 'Donji banner'),
    ]
    title = models.CharField('Naslov', max_length=150)
    image = AVIFFileField('Slika', upload_to='banners/', blank=True, max_bytes=80_000)
    secondary_image = AVIFFileField('Dodatna slika', upload_to='banners/', blank=True, max_bytes=80_000)
    eyebrow = models.CharField('Tekst iznad naslova', max_length=150, blank=True)
    features = models.CharField('Artikli ispod dugmeta', max_length=300, blank=True, help_text='Odvojite nazive znakom |, npr. iPhone|MacBook|AirPods|Apple Watch')
    subtitle = models.TextField('Opis bannera', blank=True)
    button_text = models.CharField('Tekst dugmeta', max_length=80, blank=True, default='Istraži ponudu')
    position = models.CharField('Pozicija', max_length=20, choices=POSITION_CHOICES)
    url = models.CharField('Link', max_length=300, blank=True, help_text='Lokalni link /... ili https://...')
    is_active = models.BooleanField('Prikaži banner', default=True)

    class Meta:
        verbose_name = 'Banner'
        verbose_name_plural = 'Banneri'
        ordering = ['position', 'pk']
        constraints = [models.UniqueConstraint(fields=['position'], condition=~models.Q(position='hero'), name='unique_nonhero_banner_position')]

    @property
    def feature_items(self):
        return [item.strip() for item in self.features.split('|') if item.strip()]

    def __str__(self):
        return self.title


class Order(models.Model):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="shop_orders")
    STATUS_CHOICES = [
        ('draft', 'U pripremi'), ('placed', 'Naručeno'),
        ('shipped', 'Poslato'), ('completed', 'Završeno'), ('cancelled', 'Otkazano'),
    ]
    customer_name = models.CharField('Ime i prezime', max_length=150, blank=True)
    email = models.EmailField('E-mail', blank=True)
    phone = models.CharField('Telefon', max_length=40, blank=True)
    address = models.CharField('Adresa', max_length=255, blank=True)
    city = models.CharField('Grad', max_length=100, blank=True)
    postal_code = models.CharField('Poštanski broj', max_length=20, blank=True)
    note = models.TextField('Napomena', blank=True)
    delivery_price = models.DecimalField('Cijena dostave', max_digits=10, decimal_places=2, default=0)
    loyalty_percent = models.DecimalField('Loyalty popust (%)', max_digits=5, decimal_places=2, default=0)
    loyalty_discount = models.DecimalField('Loyalty popust (KM)', max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField('Ukupno', max_digits=12, decimal_places=2, default=0)
    status = models.CharField('Status', max_length=12, choices=STATUS_CHOICES, default='placed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Narudžba'
        verbose_name_plural = 'Narudžbe'


class OrderItem(models.Model):
    promotion = models.CharField('Primijenjena akcija', max_length=600, blank=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    unit_price = models.DecimalField('Jedinična cijena', max_digits=10, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField('Količina', default=1)

    class Meta:
        verbose_name = 'Stavka narudžbe'
        verbose_name_plural = 'Stavke narudžbe'
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gte=1), name='order_item_quantity_positive')]


import uuid
import secrets

def generate_loyalty_number():
    while True:
        digits = f"{secrets.randbelow(100_000_000):08d}"
        number = f"{digits[:4]}-{digits[4:]}"
        if not LoyaltyCard.objects.filter(number=number).exists():
            return number

from django.core.validators import MaxValueValidator


class LoyaltyCard(models.Model):
    customer = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='loyalty_card')
    number = models.CharField(max_length=9, default=generate_loyalty_number, unique=True, editable=False)
    points = models.BigIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


class LoyaltyTier(models.Model):
    name = models.CharField('Naziv nivoa', max_length=80, blank=True)
    minimum = models.DecimalField('Potrošnja od (KM)', max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    maximum = models.DecimalField('Potrošnja do (KM)', max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    percent = models.DecimalField('Popust (%)', max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        ordering = ['minimum']


class AdminLoginNonce(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class SiteModule(models.Model):
    code = models.CharField('Modul', max_length=30, unique=True, choices=[('loyalty', 'Loyalty')])
    enabled = models.BooleanField('Uključen', default=True)
    paid_until = models.DateTimeField('Aktivan do', null=True, blank=True, help_text='Prazno znači bez vremenskog ograničenja.')
    revision = models.PositiveBigIntegerField(default=0, editable=False)
    updated_at = models.DateTimeField('Ažurirano', auto_now=True)

    class Meta:
        verbose_name = 'Modul'
        verbose_name_plural = 'Moduli'

    def __str__(self):
        return self.get_code_display()


class LoyaltyRules(models.Model):
    tiers_enabled = models.BooleanField('Nivoi kartice', default=True)
    points_enabled = models.BooleanField('Bodovi', default=True)
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    award_spend = models.DecimalField('Iznos kupovine (KM)', max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(Decimal('0.01'))])
    redemption_points = models.PositiveIntegerField('Broj bodova za popust', default=1, validators=[MinValueValidator(1)])
    points_per_km = models.PositiveIntegerField('Bodova za 1 KM', default=1)
    point_value = models.DecimalField('Vrijednost boda (KM)', max_digits=8, decimal_places=4, default='0.01', validators=[MinValueValidator(Decimal('0.0001'))])
    max_discount_percent = models.PositiveSmallIntegerField('Najveći popust bodovima (%)', default=50, validators=[MaxValueValidator(100)])

    @property
    def point_unit_value(self):
        return Decimal(self.point_value) / self.redemption_points

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='loyalty_rules_singleton'), models.CheckConstraint(condition=models.Q(point_value__gt=0, max_discount_percent__lte=100), name='loyalty_rules_valid')]


class LoyaltyEntry(models.Model):
    card = models.ForeignKey(LoyaltyCard, on_delete=models.PROTECT, related_name='entries')
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='point_entries')
    kind = models.CharField(max_length=12, choices=[('earn', 'Dodijeljeno'), ('redeem', 'Iskorišteno'), ('refund', 'Vraćeno'), ('reverse', 'Stornirano')])
    points = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['order', 'kind'], name='unique_order_point_operation')]


class SEOSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    site_name = models.CharField('Naziv sajta', max_length=100, default='BERA')
    home_title = models.CharField('SEO naslov početne', max_length=160, blank=True, help_text='Preporuka: oko 50–60 znakova.')
    description = models.CharField('Meta opis sajta', max_length=320, blank=True, help_text='Preporuka: oko 150–160 znakova; koristi se kada stranica nema vlastiti opis.')
    canonical_domain = models.URLField('Glavna adresa sajta', blank=True, help_text='Npr. https://www.mojshop.ba — bez putanje. Prazno koristi trenutnu domenu.')
    allow_indexing = models.BooleanField('Dozvoli indeksiranje sajta', default=True, help_text='Isključeno šalje noindex pretraživačima. Ovo nije zaštita privatnih podataka.')
    sitemap_enabled = models.BooleanField('Uključi sitemap.xml', default=True)
    structured_data = models.BooleanField('Uključi strukturirane podatke', default=True)
    social_title = models.CharField('Naslov za dijeljenje početne', max_length=160, blank=True)
    social_description = models.CharField('Opis za dijeljenje početne', max_length=320, blank=True)
    social_image = models.ImageField('Slika za društvene mreže', upload_to='seo/', blank=True, help_text='Preporuka 1200 × 630 px, JPG ili PNG.')
    favicon = models.ImageField('Ikonica sajta (favicon)', upload_to='seo/icons/', blank=True, help_text='Kvadratni PNG, preporuka 192 × 192 px ili veći.')
    twitter_handle = models.CharField('X / Twitter korisničko ime', max_length=50, blank=True, help_text='Npr. @mojshop')
    google_review_url = models.URLField('Link za Google recenzije', max_length=1000, blank=True, help_text='HTTPS link za ostavljanje recenzije vašeg Google Business profila. Poziv se prikazuje nakon uspješno kreirane narudžbe; prazno ga isključuje.')
    google_verification = models.CharField('Google Search Console verifikacijski kod', max_length=255, blank=True, help_text='Samo vrijednost content iz verifikacijske meta oznake, bez HTML-a.')
    bing_verification = models.CharField('Bing Webmaster verifikacijski kod', max_length=255, blank=True)
    contact_email = models.EmailField('Kontakt e-mail za strukturirane podatke', blank=True)
    contact_phone = models.CharField('Kontakt telefon za strukturirane podatke', max_length=50, blank=True)


class ChatBotSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField('Prikaži chatbot na početnoj', default=True)
    name = models.CharField('Naziv asistenta', max_length=60, default='BERA Asistent')
    welcome_message = models.CharField('Pozdravna poruka', max_length=500, default='Zdravo! Kako vam mogu pomoći pri odabiru artikala?')
    auto_open = models.BooleanField('Automatski otvori razgovor', default=True)
    delay_seconds = models.PositiveIntegerField('Otvori nakon (sekundi)', default=5, validators=[MinValueValidator(0), MaxValueValidator(120)])
    instructions = models.TextField('Upute za asistenta', max_length=4000, blank=True, help_text='Ton komunikacije i posebne upute. Asistent ne mijenja narudžbe i ne pristupa privatnim podacima.')
    knowledge = models.TextField('Informacije o trgovini', max_length=12000, blank=True, help_text='Unesite provjerene informacije: radno vrijeme, dostava, plaćanje, povrati i kontakt.')
    suggested_questions = models.TextField('Predložena pitanja', max_length=800, blank=True, default='Kako mogu naručiti?\nKoliko košta dostava?\nPomozi mi pronaći artikal', help_text='Svako pitanje u novom redu, prikazuje se najviše četiri.')
    support_email = models.EmailField('E-mail podrške', blank=True)
    model_name = models.CharField('OpenAI model', max_length=100, default='gpt-4.1-mini')
    guided_enabled = models.BooleanField('Vođeni odabir artikala', default=True, help_text='Kategorija → maksimalni budžet → artikli. Radi direktno s katalogom, bez AI ključa.')
    category_question = models.CharField('Pitanje za odabir kategorije', max_length=500, default='Šta želite kupiti? Odaberite kategoriju.')
    budget_question = models.CharField('Pitanje za budžet', max_length=500, default='Do koliko KM želite izdvojiti? Unesite maksimalni budžet ili odaberite iznos.')
    budget_presets = models.CharField('Ponuđeni budžeti (KM)', max_length=200, blank=True, default='300, 500, 1000, 2000', help_text='Iznosi odvojeni zarezom. Kupac uvijek može upisati drugi iznos.')
    results_message = models.CharField('Poruka uz preporučene artikle', max_length=500, default='Ponuda iz kategorije {category} do {budget} KM:', help_text='Podržano: {category}, {budget}, {count}.')
    no_results_message = models.CharField('Poruka kada nema rezultata', max_length=500, default='Trenutno nema dostupnih artikala iz kategorije {category} do {budget} KM. Pokušajte s drugim budžetom ili kategorijom.', help_text='Podržano: {category}, {budget}, {count}.')
    encrypted_api_key = models.TextField(blank=True, editable=False)
    daily_request_limit = models.PositiveIntegerField('Dnevni limit AI poruka za cijeli sajt', default=200, validators=[MinValueValidator(1), MaxValueValidator(10000)])


class ChatBotUsage(models.Model):
    key = models.CharField(max_length=100, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class CustomerContact(models.Model):
    customer = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shop_contact')
    phone = models.CharField('Telefon', max_length=16, unique=True)
    email = models.EmailField('E-mail', unique=True)
