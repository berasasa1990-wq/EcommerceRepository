"""
Brzi unos / Aktivacija artikala.

Pronađi postojeći artikal po šifri ili barkodu, dopuni cijenu, brend i sliku,
pa ga aktiviraj za webshop (na_stanju, aktivan).
"""
from __future__ import annotations

import logging
import os
import re
from decimal import Decimal, InvalidOperation

import requests
from django.db.models import Q

from .models import Brand, Category, Product, ProductImage, Tag
from .utils.images import (
    prepared_product_image_payload,
    process_quick_activation_image,
    save_prepared_product_image,
    unique_product_image_basename,
)

logger = logging.getLogger(__name__)

XAI_API_URL = 'https://api.x.ai/v1/chat/completions'
XAI_MODEL = os.environ.get('XAI_MODEL', 'grok-4.5').strip() or 'grok-4.5'


def normalize_scan_code(value: str) -> str:
    """Očisti skenirani / upisani kod (trim, bez nevidljivih znakova)."""
    if value is None:
        return ''
    text = str(value).strip()
    # skeneri ponekad dodaju enter / tab
    text = re.sub(r'[\r\n\t]+', '', text)
    return text.strip()


def find_products_by_code(code: str, *, limit: int = 20):
    """Alias — pretraga po šifri, barkodu i nazivu."""
    return find_products(code, limit=limit)


def find_products(query: str, *, limit: int = 20):
    """
    Pronađi artikle po šifri, barkodu ili nazivu.
    Prioritet: tačan kod → tačan naziv → djelomično (kod + naziv).
    """
    query = normalize_scan_code(query)
    if not query:
        return []

    base = Product.objects.select_related('brend', 'kategorija')

    # 1) Tačan match šifra / barkod
    exact_code = list(
        base.filter(
            Q(sifra__iexact=query)
            | Q(barkod__iexact=query)
            | Q(sifra_normalized__iexact=query)
            | Q(barkod_normalized__iexact=query)
        )[:limit]
    )
    if exact_code:
        return exact_code

    # Barkodovi često imaju vodeće nule
    stripped = query.lstrip('0') or query
    if stripped != query:
        exact_stripped = list(
            base.filter(
                Q(sifra__iexact=stripped)
                | Q(barkod__iexact=stripped)
                | Q(barkod__iexact=query)
                | Q(sifra__iexact=query)
            )[:limit]
        )
        if exact_stripped:
            return exact_stripped

    # 2) Tačan naziv
    exact_name = list(base.filter(naziv__iexact=query)[:limit])
    if exact_name:
        return exact_name

    # 3) Djelomično: šifra, barkod, naziv (min 2 znaka)
    if len(query) < 2:
        return []

    q_filter = (
        Q(sifra__icontains=query)
        | Q(barkod__icontains=query)
        | Q(naziv__icontains=query)
        | Q(naziv_normalized__icontains=query)
    )

    # Više riječi u nazivu — sve moraju postojati (npr. "fox rage spinner")
    words = [w for w in re.split(r'\s+', query) if len(w) >= 2]
    if len(words) > 1:
        name_and = Q()
        for w in words:
            name_and &= Q(naziv__icontains=w)
        q_filter |= name_and

    return list(base.filter(q_filter).order_by('naziv')[:limit])


def find_single_product(code: str):
    """Vrati (product, None) ako je tačno jedan match, inače (None, lista)."""
    matches = find_products(code)
    if len(matches) == 1:
        return matches[0], None
    return None, matches


def parse_price(value) -> Decimal:
    """Prihvati '12.90', '12,90', '12.90 KM'."""
    if value is None:
        raise InvalidOperation('Prazna cijena')
    text = str(value).strip().upper().replace('KM', '').replace(' ', '')
    text = text.replace(',', '.')
    if not text:
        raise InvalidOperation('Prazna cijena')
    price = Decimal(text).quantize(Decimal('0.01'))
    if price < 0:
        raise InvalidOperation('Cijena ne može biti negativna')
    return price


def xai_api_key() -> str:
    return (os.environ.get('XAI_API_KEY') or '').strip()


def generate_product_description(
    naziv: str,
    *,
    brend_naziv: str = '',
    kategorija_naziv: str = '',
) -> str:
    """
    Generiši webshop opis artikla kao da si u AI chat upisao naziv
    i tražio opis (xAI Grok / SpaceXAI — OpenAI-kompatibilan API).
    """
    naziv = (naziv or '').strip()
    if not naziv:
        raise ValueError('Naziv artikla je prazan.')

    api_key = xai_api_key()
    if not api_key:
        raise RuntimeError(
            'XAI_API_KEY nije postavljen u .env — dodaj ključ sa https://console.x.ai',
        )

    context_bits = []
    if brend_naziv:
        context_bits.append(f'Brend: {brend_naziv}')
    if kategorija_naziv:
        context_bits.append(f'Kategorija: {kategorija_naziv}')
    context_line = ('\n' + '\n'.join(context_bits)) if context_bits else ''

    user_prompt = (
        f'Napiši opis za artikal za webshop ribolovne opreme.\n\n'
        f'Naziv artikla: {naziv}'
        f'{context_line}\n\n'
        f'Zadatak: kao da sam ti u chatu napisao samo naziv i rekao '
        f'„napiši opis za ovaj artikal za online prodaju”.\n'
        f'Pravila:\n'
        f'- jezik: bosanski/hrvatski (BiH webshop), prirodno i jasno\n'
        f'- 2 do 4 rečenice, max ~80 riječi\n'
        f'- korisno kupcu (za šta služi, za koga, ključne prednosti)\n'
        f'- bez pretjeranog marketinga, bez emoji, bez hashtagova\n'
        f'- bez naslova, bez markdowna, bez navodnika oko cijelog teksta\n'
        f'- samo čist tekst opisa spreman za polje Opis na stranici artikla'
    )

    payload = {
        'model': XAI_MODEL,
        'messages': [
            {
                'role': 'system',
                'content': (
                    'Ti si copywriter za webshop opremazaribolov.ba. '
                    'Pišeš kratke, tačne opise artikala ribolovne opreme. '
                    'Odgovaraš isključivo tekstom opisa, ničim drugim.'
                ),
            },
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': 0.6,
        'max_tokens': 350,
    }

    try:
        resp = requests.post(
            XAI_API_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=35,
        )
    except requests.RequestException as exc:
        logger.exception('AI opis: mrežna greška')
        raise RuntimeError(f'AI nije dostupan: {exc}') from exc

    if resp.status_code >= 400:
        detail = (resp.text or '')[:300]
        logger.error('AI opis: HTTP %s %s', resp.status_code, detail)
        raise RuntimeError(
            f'AI opis nije uspio (HTTP {resp.status_code}). '
            'Provjeri XAI_API_KEY i kredit na console.x.ai.',
        )

    data = resp.json()
    try:
        text = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('AI je vratio neočekivan odgovor.') from exc

    text = (text or '').strip()
    # skini eventualne omotajuće navodnike
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'„“':
        text = text[1:-1].strip()
    # ukloni markdown bold/naslove ako model ipak doda
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = text.strip()
    if not text:
        raise RuntimeError('AI je vratio prazan opis.')
    return text


def parse_tag_names(raw: str) -> list[str]:
    """'casting, feeder, štap' → lista očišćenih naziva."""
    if not raw:
        return []
    parts = re.split(r'[,;\n]+', str(raw))
    names = []
    seen = set()
    for part in parts:
        name = (part or '').strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name[:50])
    return names


def resolve_tags(raw: str) -> list[Tag]:
    """Pronađi ili kreiraj tagove iz unosa (zarez / novi red)."""
    tags = []
    for name in parse_tag_names(raw):
        try:
            tag, _ = Tag.get_or_create_by_name(name)
            tags.append(tag)
        except Exception:
            logger.exception('Brzi unos: tag nije kreiran: %s', name)
    return tags


def category_choices():
    """Lista kategorija za select: id + label s putanjom (Roditelj → Dijete)."""
    choices = []
    qs = (
        Category.objects.filter(aktivan=True)
        .select_related('roditelj', 'roditelj__roditelj')
        .order_by('redoslijed', 'naziv')
    )
    for category in qs:
        parts = []
        cur = category
        seen = set()
        while cur is not None and cur.pk not in seen:
            seen.add(cur.pk)
            parts.append(cur.naziv or f'#{cur.pk}')
            cur = getattr(cur, 'roditelj', None)
        parts.reverse()
        choices.append({'id': category.pk, 'label': ' → '.join(parts)})
    return choices


def take_off_stock(product: Product) -> Product:
    """
    Skini artikal sa stanja (jedan klik).
    Samo na_stanju=False — ništa drugo se ne dira (cijena, slika, opis, stanje, aktivan…).
    Kupcima nestaje iz liste i sa stranice artikla (filter na_stanju=True).
    """
    if not product.na_stanju:
        return product
    product.na_stanju = False
    product.save(update_fields=['na_stanju', 'azuriran'])
    return product


def activate_product(
    product: Product,
    *,
    cijena: Decimal,
    brend: Brand | None,
    kategorija: Category | None = None,
    image_upload=None,
    keep_existing_image: bool = False,
    opis: str | None = None,
    tagovi: list[Tag] | None = None,
    barkod: str | None = None,
    extra_images=None,
    pakovanje_komada: int | None = None,
    set_pakovanje: bool = False,
    proizvedeno_u_japanu: bool | None = None,
) -> Product:
    """
    Aktiviraj postojeći artikal za webshop:
    - cijena, brend, kategorija, opcionalno opis, tagovi, barkod
    - pakovanje (komada) i Made in Japan kad se proslijede
    - na_stanju=True, aktivan=True
    - stanje min 1 ako je bilo 0
    - opcionalno nova glavna slika + dodatne slike (galerija)
    """
    product.cijena = cijena
    product.brend = brend
    if kategorija is not None:
        product.kategorija = kategorija
    product.na_stanju = True
    product.aktivan = True
    if not product.stanje or product.stanje < 1:
        product.stanje = 1
    if opis is not None:
        product.opis = (opis or '').strip()
    if barkod is not None:
        product.barkod = (barkod or '').strip()[:50]
    if set_pakovanje:
        # None / 0 / 1 = po komadu (bez pakovanja)
        n = int(pakovanje_komada or 0)
        product.pakovanje_komada = n if n > 1 else None
    if proizvedeno_u_japanu is not None:
        product.proizvedeno_u_japanu = bool(proizvedeno_u_japanu)

    # Prvo polja (bez otvaranja stare slike ako fajl fali na disku)
    if image_upload and not keep_existing_image:
        base = unique_product_image_basename(
            (product.slug or product.naziv or 'artikal'),
            fallback='artikal',
        )
        safe_name = f'{base}.jpg'
        processed = process_quick_activation_image(image_upload, filename=safe_name)
        prepared = prepared_product_image_payload(processed)
        # Sačuvaj polja prvo, pa novu sliku (save_prepared briše staru putanju ako postoji)
        product.save()
        try:
            save_prepared_product_image(product.slika, prepared)
        except FileNotFoundError:
            # Stara putanja u bazi bez fajla — očisti ime i snimi novu
            logger.warning(
                'Stara slika ne postoji na disku (%s); snimam novu.',
                getattr(product.slika, 'name', None),
            )
            product.slika.name = ''
            product.slika._file = None
            product.slika._committed = True
            save_prepared_product_image(product.slika, prepared)
        product.save(update_fields=['slika', 'azuriran'])
    else:
        product.save()

    # Samo dodaj unesene tagove (ne briši postojeće ako je lista prazna)
    if tagovi:
        product.tagovi.add(*tagovi)

    # Dodatne slike (opcionalno) — ProductImage.save() radi image processing
    uploads = list(extra_images or [])
    if uploads:
        from django.db.models import Max

        max_order = (
            product.dodatne_slike.aggregate(max_red=Max('redoslijed')).get('max_red') or 0
        )
        for index, upload in enumerate(uploads, start=1):
            if not upload:
                continue
            content_type = (getattr(upload, 'content_type', None) or '').lower()
            name = (getattr(upload, 'name', None) or '').lower()
            if content_type and not content_type.startswith('image/'):
                if not any(name.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif', '.heic')):
                    continue
            try:
                ProductImage.objects.create(
                    product=product,
                    slika=upload,
                    redoslijed=max_order + index,
                )
            except Exception:
                logger.exception(
                    'Brzi unos: dodatna slika nije snimljena za product_id=%s',
                    product.pk,
                )

    return product
