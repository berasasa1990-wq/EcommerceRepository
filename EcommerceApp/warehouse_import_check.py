"""Read-only comparison of Excel article codes against all product names."""
import csv
import io
import unicodedata

MAX_ROWS = 5000
MAX_CHARACTERS = 2_000_000


def normalize(value):
    return unicodedata.normalize('NFC', value).casefold()


def parse_excel_codes(pasted):
    if len(pasted) > MAX_CHARACTERS:
        raise ValueError('Unos je prevelik. Zalijepi najviše 5.000 redova odjednom.')
    codes = []
    seen = set()
    nonempty_rows = 0
    has_header = False
    try:
        rows = csv.reader(io.StringIO(pasted, newline=''), delimiter='\t', strict=True)
        for line, row in enumerate(rows, 1):
            if not any(cell.strip() for cell in row):
                continue
            nonempty_rows += 1
            if nonempty_rows > MAX_ROWS + 1:
                raise ValueError('Zalijepi najviše 5.000 redova odjednom.')
            if len(row) != 3:
                raise ValueError(f'Red {line}: očekivane su 3 kolone, pronađeno {len(row)}. Kopiraj tri kolone direktno iz Excela.')
            code = row[0].strip().lstrip('\ufeff')
            key = normalize(code)
            if nonempty_rows == 1 and key in {'šifra', 'sifra', 'sku', 'code'}:
                has_header = True
                continue
            if nonempty_rows - int(has_header) > MAX_ROWS:
                raise ValueError('Zalijepi najviše 5.000 redova odjednom.')
            if not code:
                raise ValueError(f'Red {line}: šifra u prvoj koloni je prazna.')
            if len(code) > 500:
                raise ValueError(f'Red {line}: šifra je preduga (najviše 500 znakova).')
            if key not in seen:
                codes.append(code)
                seen.add(key)
    except csv.Error as exc:
        raise ValueError('Neispravan unos. Kopiraj tri kolone direktno iz Excela.') from exc
    if not codes:
        raise ValueError('Zalijepi barem jedan red sa šifrom i tri kolone iz Excela.')
    return codes


def missing_name_codes(codes, product_names):
    remaining = {normalize(code) for code in codes}
    for name in product_names:
        folded = normalize(name or '')
        remaining.difference_update(code for code in tuple(remaining) if code in folded)
        if not remaining:
            break
    return [code for code in codes if normalize(code) in remaining]


def parse_bulk_names(pasted):
    if len(pasted) > MAX_CHARACTERS:
        raise ValueError('Unos je prevelik.')
    names = []
    seen = set()
    try:
        for line, row in enumerate(csv.reader(io.StringIO(pasted, newline=''), delimiter='\t', strict=True), 1):
            if not any(value.strip() for value in row):
                continue
            name = row[0].strip().lstrip('\ufeff')
            if not names and normalize(name) in {'naziv', 'naziv artikla'}:
                continue
            if not name or len(name) > 200:
                raise ValueError(f'Red {line}: naziv mora imati između 1 i 200 znakova.')
            if normalize(name) not in seen:
                names.append(name)
                seen.add(normalize(name))
            if len(names) > 400:
                raise ValueError('Dodaj najviše 400 artikala odjednom.')
    except csv.Error as exc:
        raise ValueError('Neispravan unos naziva iz Excela.') from exc
    if not names:
        raise ValueError('Zalijepi barem jedan naziv artikla.')
    return names


def detect_brand(name, brands):
    """Match full brand names; a longer brand wins over its contained prefix."""
    import re
    folded = normalize(name)
    matches = [brand for brand in brands if brand['naziv'].strip() and
               re.search(r'(?<!\w)' + re.escape(normalize(brand['naziv'].strip())) + r'(?!\w)', folded)]
    matches = [brand for brand in matches if not any(
        brand['id'] != other['id'] and normalize(brand['naziv']) != normalize(other['naziv'])
        and normalize(brand['naziv']) in normalize(other['naziv']) for other in matches
    )]
    return matches[0]['id'] if len(matches) == 1 else None


def save_bulk_products(rows):
    from decimal import Decimal
    from django.db import transaction
    from django.utils import timezone
    from .models import Product, Brand

    if not rows or len(rows) > 400:
        raise ValueError('Dodaj između 1 i 400 artikala odjednom.')
    brand_ids = set(Brand.objects.values_list('pk', flat=True))
    cleaned = []
    for index, row in enumerate(rows, 1):
        name = row['naziv'].strip()
        raw_brand = str(row.get('brand_id') or '').strip()
        if not name or len(name) > 200:
            raise ValueError(f'Red {index}: naziv mora imati između 1 i 200 znakova.')
        if not raw_brand.isdigit() or int(raw_brand) not in brand_ids:
            raise ValueError(f'Red {index}: odaberi postojeći brend.')
        cleaned.append((name, int(raw_brand), str(row.get('opis', ''))))
    created = []
    skipped = []
    with transaction.atomic():
        # Serialize submissions on PostgreSQL; a replay must not duplicate names.
        from django.db import connection
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [2026101501])
        existing = {}
        for pk, name in Product.objects.order_by('pk').values_list('pk', 'naziv').iterator():
            existing.setdefault(normalize(name), []).append(pk)
        for name, brand_id, description in cleaned:
            key = normalize(name)
            if key in existing:
                if description.strip():
                    if len(existing[key]) != 1:
                        raise ValueError(f'{name}: pronađeno više artikala istog naziva; opis nije moguće sigurno povezati.')
                    product = Product.objects.select_for_update().get(pk=existing[key][0])
                    product.opis = description
                    product.save(update_fields=['opis'])
                skipped.append(name)
                continue
            product = Product.objects.create(
                naziv=name, brend_id=brand_id, opis=description, cijena=Decimal('0.00'),
                stanje=0, na_stanju=False, sakriven_do_stanja=True,
                prikazi_na_pocetnoj=False, magacin_sync_at=timezone.now(),
            )
            existing[key] = [product.pk]
            created.append(product)
    return created, skipped


def parse_bulk_descriptions(pasted):
    import re
    if len(pasted) > MAX_CHARACTERS:
        raise ValueError('Unos je prevelik.')
    rows = []
    seen = set()
    for number, line in enumerate(pasted.splitlines(), 1):
        if not line.strip():
            continue
        parts = re.split(r'\s+[-–—]\s+', line.strip(), maxsplit=1)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(f'Red {number}: koristi format šifra - opis.')
        code, description = (part.strip() for part in parts)
        if normalize(code) in seen:
            raise ValueError(f'Red {number}: šifra {code} je ponovljena.')
        seen.add(normalize(code))
        rows.append((code, description))
        if len(rows) > 400:
            raise ValueError('Dodaj najviše 400 opisa odjednom.')
    if not rows:
        raise ValueError('Zalijepi barem jedan red šifra - opis.')
    return rows


def match_description_product(code, products):
    import re
    key = normalize(code)
    pattern = re.compile(r'(?<![\w/-])' + re.escape(key) + r'(?![\w/-])')
    return [product for product in products if
            normalize(product.sifra or '') == key or pattern.search(normalize(product.naziv))]


def save_bulk_descriptions(pasted):
    from django.db import transaction
    from .models import Product
    rows = parse_bulk_descriptions(pasted)
    with transaction.atomic():
        products = list(Product.objects.only('pk', 'naziv', 'sifra').order_by('pk'))
        matched = []
        errors = []
        used = set()
        for code, description in rows:
            matches = match_description_product(code, products)
            if len(matches) != 1:
                errors.append(f'{code}: ' + ('nije pronađen artikal' if not matches else 'pronađeno više artikala'))
            elif matches[0].pk in used:
                errors.append(f'{code}: isti artikal je naveden pod više šifri')
            else:
                used.add(matches[0].pk)
                matched.append((matches[0].pk, description))
        if errors:
            raise ValueError('Ništa nije spremljeno. ' + '; '.join(errors))
        for pk, description in sorted(matched):
            # Use save so existing change-history hooks remain active; escape plain text
            # because product descriptions may be rendered as HTML on the storefront.
            from django.utils.html import escape
            product = Product.objects.select_for_update().get(pk=pk)
            product.opis = str(escape(description))
            product.save(update_fields=['opis'])
    return len(matched)



def prepare_bulk_descriptions(rows, pasted):
    from types import SimpleNamespace
    from django.utils.html import escape
    if not rows:
        raise ValueError('Prvo pripremi nazive i brendove artikala.')
    products = [SimpleNamespace(pk=index, naziv=row['naziv'], sifra='') for index, row in enumerate(rows)]
    descriptions = {}
    for code, description in parse_bulk_descriptions(pasted):
        matches = match_description_product(code, products)
        if len(matches) != 1:
            raise ValueError(f'{code}: ' + ('šifra nije pronađena u pripremljenim nazivima.' if not matches else 'šifra odgovara više pripremljenih artikala.'))
        index = matches[0].pk
        if index in descriptions:
            raise ValueError(f'{code}: isti artikal je naveden pod više šifri.')
        descriptions[index] = str(escape(description))
    missing = [row['naziv'] for index, row in enumerate(rows) if index not in descriptions]
    if missing:
        raise ValueError('Nedostaje opis za: ' + '; '.join(missing))
    return [dict(row, opis=descriptions[index]) for index, row in enumerate(rows)]


def excel_import_values(pasted):
    """Keep quantity/invoice cells for the later bulk draft (no stock changes)."""
    parse_excel_codes(pasted)
    values = []
    seen = set()
    for row in csv.reader(io.StringIO(pasted, newline=''), delimiter='\t', strict=True):
        if not any(cell.strip() for cell in row):
            continue
        code = row[0].strip().lstrip('\ufeff')
        if normalize(code) in {'šifra', 'sifra', 'sku', 'code'} or normalize(code) in seen:
            continue
        seen.add(normalize(code))
        values.append({'code': code, 'kolicina': row[1].strip(), 'fakturna': row[2].strip()})
    return values


def attach_import_values(rows, values):
    from types import SimpleNamespace
    products = [SimpleNamespace(pk=i, naziv=row['naziv'], sifra='') for i, row in enumerate(rows)]
    result = [dict(row) for row in rows]
    for value in values:
        matches = match_description_product(value['code'], products)
        if len(matches) == 1:
            result[matches[0].pk].update({key: value[key] for key in ('kolicina', 'fakturna')})
    return result


def save_bulk_import_history(rows, created, skipped, user):
    from decimal import Decimal, InvalidOperation
    from .models import Uvoz, UvozStavka

    def number(value, places):
        raw = str(value or '').strip().replace('\u00a0', '').replace(' ', '')
        if not raw:
            return None
        if ',' in raw:
            raw = raw.replace('.', '').replace(',', '.')
        try:
            result = Decimal(raw)
            if not result.is_finite() or result < 0 or result >= Decimal(10) ** (12 - places) or result != result.quantize(Decimal(10) ** -places):
                raise InvalidOperation
            return result
        except InvalidOperation as exc:
            raise ValueError(f'Neispravna količina ili fakturna vrijednost: {value}') from exc

    lines = [(row, number(row.get('kolicina'), 3), number(row.get('fakturna'), 4)) for row in rows]
    uvoz = Uvoz.objects.create(
        naziv='Novi artikli bulk', izvor=Uvoz.Izvor.MAGACIN, kreirao=user,
        broj_redova=len(rows), broj_kreirano=len(created), broj_azurirano=len(skipped),
        zaliha_primljena=False, napomena='Bulk unos naziva, brenda i opisa. Količine su iz izvornog Excela; ovaj unos ne mijenja lager.',
    )
    for index, (row, qty, invoice) in enumerate(lines, 1):
        UvozStavka.objects.create(uvoz=uvoz, artikal_naziv=row['naziv'], kolicina=qty,
                                 fakturna=invoice, redoslijed=index, status=UvozStavka.Status.CREATED if row['naziv'] not in skipped else UvozStavka.Status.UPDATED)
    return uvoz


def import_history_tsv(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter='\t', lineterminator='\n')
    writer.writerow(['Naziv artikla', 'Količina', 'Fakturna vrijednost'])
    for row in rows:
        name = row.artikal_naziv
        if name.lstrip().startswith(('=', '+', '-', '@')):
            name = "'" + name
        writer.writerow([name, '' if row.kolicina is None else format(row.kolicina, 'f'),
                         '' if row.fakturna is None else format(row.fakturna, 'f')])
    return output.getvalue()
