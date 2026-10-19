"""Read-only Gmail access and conservative payment amount extraction."""
import hashlib
import imaplib
import json
import re
import ssl
import unicodedata
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

PAGE_SIZE = 20
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_BODY_CHARS = 60_000


class CompanyMailError(Exception):
    pass


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.ignored += 1
        elif not self.ignored and tag in ('p', 'br', 'div', 'tr', 'li', 'td'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self.ignored = max(0, self.ignored - 1)
        elif not self.ignored and tag in ('p', 'div', 'tr', 'li', 'td'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def normalize_phrase(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def _number(value):
    value = re.sub(r'[\s\u00a0]', '', value)
    if ',' in value and '.' in value:
        decimal_separator = ',' if value.rfind(',') > value.rfind('.') else '.'
        value = value.replace('.' if decimal_separator == ',' else ',', '')
        value = value.replace(decimal_separator, '.')
    elif ',' in value or '.' in value:
        separator = ',' if ',' in value else '.'
        if len(value.rsplit(separator, 1)[1]) == 3:
            value = value.replace(separator, '')
        else:
            value = value.replace(separator, '.')
    try:
        number = Decimal(value)
        return number.quantize(Decimal('.01')) if number >= 0 else None
    except InvalidOperation:
        return None


_AMOUNT = r'(?<![\w.,+\-])[+\-]?(?:\d{1,3}(?:[. ,\u00a0]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)(?![\w.,])'
_CURRENCY = r'(?:BAM|KM|EUR|€|USD|\$)'
_PAYMENT_LABEL = r'(?:iznos\s+(?:uplate|transakcije|priliva|priljeva)|upla[ćc]eno|uplata|priliv|priljev|odobrenje|amount\s+paid|payment\s+amount)'


def payment_amounts(subject, body):
    """Show explicit payment amounts; other money values are only candidates."""
    text = subject + '\n' + body
    results = []
    seen = {}
    expressions = [
        re.compile(r'(?P<amount>' + _AMOUNT + r')\s*(?P<currency>' + _CURRENCY + r')(?!\w)', re.I),
        re.compile(r'(?<!\w)(?P<currency>' + _CURRENCY + r')\s*(?P<amount>' + _AMOUNT + r')', re.I),
    ]
    for expression in expressions:
        for match in expression.finditer(text):
            value = _number(match.group('amount'))
            if value is None:
                continue
            currency = match.group('currency').upper()
            currency = {'KM': 'BAM', '€': 'EUR', '$': 'USD'}.get(currency, currency)
            key = (value, currency)
            before = text[max(0, match.start() - 70):match.start()]
            explicit = bool(re.search(_PAYMENT_LABEL + r'\s*[:=]?\s*$', before, re.I))
            if key in seen:
                seen[key]['explicit'] = seen[key]['explicit'] or explicit
                continue
            amount = {'value': value, 'currency': currency, 'explicit': explicit}
            seen[key] = amount
            results.append(amount)
            if len(results) >= 12:
                return results
    return results


def excluded_mail_subject(subject):
    return 'cms' in normalize_phrase(subject)


def parse_message(raw, uid, phrase=''):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(message.get('Subject', '(Bez naslova)'))
    if excluded_mail_subject(subject):
        return None
    body_part = message.get_body(preferencelist=('plain', 'html'))
    body = ''
    if body_part:
        try:
            body = body_part.get_content()
        except (LookupError, UnicodeError):
            body = (body_part.get_payload(decode=True) or b'').decode('utf-8', errors='replace')
        if not isinstance(body, str):
            body = ''
        if body_part.get_content_type() == 'text/html':
            parser = _PlainHTML()
            parser.feed(body)
            body = ''.join(parser.parts)
    body = body[:MAX_BODY_CHARS].strip()
    try:
        date = parsedate_to_datetime(str(message.get('Date', '')))
        if date and timezone.is_naive(date):
            date = timezone.make_aware(date)
    except (ValueError, TypeError, OverflowError):
        date = None
    statement_rows = []
    warnings = []
    if phrase or phrase is None:
        from .company_statements import statement_matches, pdf_statement_matches
        if phrase is not None or 'carpologija' in normalize_phrase(body[:2500]):
            statement_rows.extend(statement_matches(body, phrase, 'Tekst emaila', subject))
        for part in message.walk():
            filename = str(part.get_filename() or '')
            if part.get_content_type() != 'application/pdf' and not filename.lower().endswith('.pdf'):
                continue
            try:
                rows, warning = pdf_statement_matches(part.get_payload(decode=True) or b'', phrase,
                                                      filename or 'PDF izvod', subject)
                statement_rows.extend(rows)
                if warning:
                    warnings.append(warning)
            except Exception:
                warnings.append('Jedan PDF prilog nije moguće pročitati.')
        if not statement_rows:
            return None
        amounts = [{'value': row['amount'], 'currency': row['currency'], 'explicit': True}
                   for row in statement_rows if row['amount'] is not None]
    else:
        amounts = payment_amounts(subject, body)
    return {
        'uid': uid, 'subject': subject, 'sender': str(message.get('From', '')),
        'date': date, 'body': body, 'amounts': amounts,
        'statement_matches': statement_rows, 'statement_warnings': warnings,
        'payment_amounts': [amount for amount in amounts if amount['explicit']],
        'attachments': [str(part.get_filename()) for part in message.walk() if part.get_filename()],
        'truncated': len(raw) >= MAX_MESSAGE_BYTES,
    }


def fetch_company_mail(phrase='', page=1, since_uid=0):
    user = settings.COMPANY_MAIL_USER
    password = settings.COMPANY_MAIL_APP_PASSWORD
    if not user or not password:
        raise CompanyMailError('Email nije povezan. Na serveru postavi COMPANY_MAIL_USER i COMPANY_MAIL_APP_PASSWORD.')
    all_statements = phrase is None
    phrase = None if all_statements else ' '.join((phrase or '').split())
    if phrase is not None and len(phrase) > 160:
        raise CompanyMailError('Filter fraza može imati najviše 160 znakova.')
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    identity = hashlib.sha256((user + password + settings.COMPANY_MAIL_FOLDER).encode()).hexdigest()
    cache_key = 'company-mail-all-credits-v1:' + hashlib.sha256(f'{identity}:{phrase}:{page}:{since_uid}'.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    client = None
    try:
        client = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=15)
        client.login(user, password)
        status, _ = client.select(settings.COMPANY_MAIL_FOLDER, readonly=True)
        if status != 'OK':
            raise CompanyMailError('Email folder nije dostupan. Provjeri COMPANY_MAIL_FOLDER.')
        if phrase or all_statements:
            # Literal prevents search injection and supports non-ASCII names.
            search_phrase = phrase.split()[0] if phrase in ('ribarska pri', 'ribolovacka pri') else phrase
            query = '(subject:izvod OR subject:statement OR filename:pdf)' if all_statements else json.dumps(search_phrase, ensure_ascii=False) + ' (subject:izvod OR subject:statement OR filename:pdf)'
            client.literal = query.encode('utf-8')
            uid_limit = ['UID', f'{int(since_uid) + 1}:*'] if since_uid else []
            status, data = client.uid('search', None, 'NOT', 'SUBJECT', '"CMS"', *uid_limit, 'X-GM-RAW')
        else:
            status, data = client.uid('search', None, 'NOT', 'SUBJECT', '"CMS"')
        if status != 'OK':
            raise CompanyMailError('Gmail nije izvršio pretragu poruka. Pokušaj ponovo.')
        uids = [uid for uid in (data[0] or b'').split()[::-1] if int(uid) > int(since_uid)]
        pages = max(1, (len(uids) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        selected = uids[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        messages = []
        for offset in range(0, len(selected), 4):
            batch = selected[offset:offset + 4]
            # Check only subject headers before downloading any message bodies.
            status, headers = client.uid('fetch', b','.join(batch).decode('ascii'),
                                         '(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])')
            if status != 'OK':
                raise CompanyMailError('Naslove poruka trenutno nije moguće provjeriti.')
            allowed = []
            for header in headers or []:
                if not isinstance(header, tuple) or not isinstance(header[1], bytes):
                    continue
                uid_match = re.search(rb'UID\s+(\d+)', header[0])
                meta = BytesParser(policy=policy.default).parsebytes(header[1], headersonly=True)
                if uid_match and not excluded_mail_subject(str(meta.get('Subject', ''))):
                    allowed.append(uid_match.group(1))
            if not allowed:
                continue
            status, rows = client.uid('fetch', b','.join(allowed).decode('ascii'),
                                      f'(UID BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)')
            if status != 'OK':
                raise CompanyMailError('Poruke trenutno nije moguće učitati. Pokušaj ponovo.')
            for row in rows or []:
                if not isinstance(row, tuple) or not isinstance(row[1], bytes):
                    continue
                match = re.search(rb'UID\s+(\d+)', row[0])
                uid = match.group(1).decode() if match else ''
                parsed = parse_message(row[1], uid, phrase)
                if parsed:
                    messages.append(parsed)
        messages.sort(key=lambda message: int(message['uid'] or 0), reverse=True)
        result = {'messages': messages, 'page': page, 'pages': pages,
                  'has_previous': page > 1, 'has_next': page < pages,
                  'previous_page': page - 1, 'next_page': page + 1, 'scanned_count': len(selected),
                  'updated_at': timezone.now(), 'latest_uid': max([int(uid) for uid in uids] or [int(since_uid)])}
        cache.set(cache_key, result, 60)
        return result
    except imaplib.IMAP4.error:
        raise CompanyMailError('Gmail nije prihvatio prijavu ili upit. Provjeri App Password i IMAP pristup na Google računu.') from None
    except (OSError, TimeoutError):
        raise CompanyMailError('Povezivanje s Gmailom trenutno nije uspjelo. Pokušaj ponovo.') from None
    finally:
        if client:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
