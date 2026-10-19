"""Extract matching transaction blocks, keeping credits separate from debits."""
import re
import unicodedata


_MONEY = re.compile(r'(?<![\w.,])[-+]?(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d[\d.,]*)[.,]\d{2}(?![\w.,])')
_ACCOUNT = re.compile(r'^\s*\d{3}-?\d{3}-?\d{8}-?\d{2}\b')
_DATE = re.compile(r'^\s*\d{2}\.\d{2}\.\d{4}\b')


def statement_normalize(text):
    return ' '.join(''.join(char for char in unicodedata.normalize('NFKD', text.casefold().replace('đ', 'dj'))
                            if not unicodedata.combining(char)).split())


def _payer_name(block_lines, account_layout):
    date_prefix = r'^\s*\d{2}\.\d{2}\.\d{2,4}\.?\s*'
    if account_layout:
        for line in block_lines[1:]:
            if re.match(date_prefix, line):
                name = re.sub(date_prefix, '', line).strip()
                name = re.split(r'\s{2,}', name)[0].strip()
                if name:
                    return name[:160]
    for line in block_lines:
        candidate = re.sub(date_prefix, '', line)
        match = re.match(r'^\s*/?\d[\d-]{9,}\s+(.+)', candidate)
        if match:
            name = _MONEY.split(match.group(1))[0].strip()
            name = re.split(r'\s{2,}', name)[0].strip()
            if name and re.search(r'[^\W\d_]', name):
                return name[:160]
    for line in reversed(block_lines[1:]):
        name = re.sub(date_prefix, '', line).strip()
        if name and not re.search(r'prethodn|ukupan|saldo|napomena', statement_normalize(name)):
            if re.search(r'[^\W\d_]', name) and not _MONEY.search(name):
                return name[:160]
    name = _MONEY.split(re.sub(date_prefix, '', block_lines[0]))[0].strip()
    return name[:160]


def statement_matches(text, phrase, source, subject=''):
    from .company_mail import _number, payment_amounts

    needle = statement_normalize(phrase or '')
    if phrase is not None and (not needle or needle not in statement_normalize(text)):
        return []
    normalized = statement_normalize(text)
    currency_match = re.search(r'\b(BAM|KM|EUR|USD)\b', subject + ' ' + text, re.I)
    currency = currency_match.group(1).upper() if currency_match else ''
    if currency == 'KM':
        currency = 'BAM'
    # NLB: account and debit/credit on first line; payer on following line.
    nlb = bool(re.search(r'duguje\s+potrazuje', normalized))
    # Other domestic statements: date/description then debit, credit, balance.
    other = 'na teret' in normalized and 'u korist' in normalized
    statement_title = statement_normalize(subject)
    if not (nlb or other or re.search(r'izvod|statement|promet.*racun|izvjestaj.*promjen', statement_title + ' ' + normalized)):
        return []
    start_pattern = (_ACCOUNT if any(_ACCOUNT.search(line) for line in text.splitlines()) else _DATE) if nlb else _DATE if other else None
    lines = text.splitlines()
    matches = []
    if start_pattern:
        starts = [i for i, line in enumerate(lines) if start_pattern.search(line)]
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            block_lines = lines[start:end]
            # Summary/footer text is never part of a counterparty transaction.
            for offset, line in enumerate(block_lines[1:], 1):
                if re.search(r'prethodno stanje|ukupno duguje|ukupan promet|napomena|dozvoljeno', statement_normalize(line)):
                    block_lines = block_lines[:offset]
                    break
            block = '\n'.join(block_lines).strip()
            if needle and needle not in statement_normalize(block):
                continue
            values = _MONEY.findall(lines[start])
            if len(values) < (2 if nlb else 3):
                matches.append({'source': source, 'text': block, 'amount': None, 'currency': currency})
                continue
            credit = _number(values[1] if nlb else values[-2])
            if credit is not None and credit > 0:
                matches.append({'source': source, 'text': block, 'amount': credit, 'currency': currency,
                                'payer': _payer_name(block_lines, start_pattern is _ACCOUNT)})
        return matches
    if phrase is None:
        return matches
    # Unknown layouts: do not guess which balance/column belongs to the payer.
    for index, line in enumerate(lines):
        if needle not in statement_normalize(line):
            continue
        block = '\n'.join(lines[max(0, index - 1):min(len(lines), index + 3)]).strip()
        explicit = [row for row in payment_amounts('', line) if row['explicit']]
        matches.append({'source': source, 'text': block,
                        'amount': explicit[0]['value'] if len(explicit) == 1 else None,
                        'currency': explicit[0]['currency'] if len(explicit) == 1 else currency})
    return matches


def pdf_statement_matches(raw, phrase, source, subject=''):
    from io import BytesIO
    from pypdf import PdfReader

    if raw.lstrip().startswith(b'JVBER'):
        import base64
        try:
            raw = base64.b64decode(b''.join(raw.split()), validate=True)
        except ValueError:
            return [], 'PDF prilog nije ispravno kodiran.'
    if b'%PDF-' not in raw[:1024]:
        return [], 'Prilog nije prepoznat kao PDF izvod.'
    if b'%%EOF' not in raw[-2048:]:
        return [], 'PDF prilog nije potpun i nije obrađen.'
    reader = PdfReader(BytesIO(raw), strict=False)
    if reader.is_encrypted and not reader.decrypt(''):
        return [], 'PDF izvod je zaštićen lozinkom.'
    if phrase is None:
        headers = []
        for page in reader.pages[:2]:
            lines = (page.extract_text(extraction_mode='layout') or '').splitlines()
            end = next((index for index, line in enumerate(lines) if _ACCOUNT.search(line) or _DATE.search(line)), len(lines))
            headers.append('\n'.join(lines[:end])[:5000])
        owner_text = '\n'.join(headers)
        if 'carpologija' not in statement_normalize(owner_text):
            return [], 'Izvod nije prepoznat kao izvod računa Carpologije.'
    matches = []
    readable = False
    for number, page in enumerate(reader.pages[:50], 1):
        text = (page.extract_text(extraction_mode='layout') or '')[:200_000]
        readable = readable or bool(text.strip())
        matches.extend(statement_matches(text, phrase, f'{source} · strana {number}', subject))
    warning = '' if readable else 'PDF izvod je skeniran i nema čitljiv tekst.'
    if len(reader.pages) > 50:
        warning = 'Pregledano je prvih 50 stranica PDF izvoda.'
    return matches, warning
