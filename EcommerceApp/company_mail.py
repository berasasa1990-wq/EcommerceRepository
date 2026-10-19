"""Read-only Gmail access and conservative payment amount extraction."""
import hashlib
import imaplib
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
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
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


_AMOUNT = r'(?<![\w.,])(?:\d{1,3}(?:[. ,\u00a0]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)(?![\w.,])'
_CURRENCY = r'(?:BAM|KM|EUR|€|USD|\$)'
_PAYMENT_LABEL = r'(?:iznos\s+(?:uplate|transakcije|priliva|priljeva)|upla[ćc]eno|uplata|priliv|priljev|odobrenje|amount\s+paid|payment\s+amount)'


def payment_amounts(subject, body):
    """Show explicit payment amounts; other money values are only candidates."""
    text = subject + '\n' + body
    results = []
    seen = set()
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
            if key in seen:
                continue
            seen.add(key)
            before = text[max(0, match.start() - 70):match.start()]
            explicit = bool(re.search(_PAYMENT_LABEL + r'\s*[:=\-]?\s*$', before, re.I))
            results.append({'value': value, 'currency': currency, 'explicit': explicit})
            if len(results) >= 12:
                return results
    return results


def parse_message(raw, uid, phrase=''):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(message.get('Subject', '(Bez naslova)'))
    if normalize_phrase(phrase) not in normalize_phrase(subject):
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
    amounts = payment_amounts(subject, body)
    return {
        'uid': uid, 'subject': subject, 'sender': str(message.get('From', '')),
        'date': date, 'body': body, 'amounts': amounts,
        'payment_amounts': [amount for amount in amounts if amount['explicit']],
        'attachments': [str(part.get_filename()) for part in message.walk() if part.get_filename()],
        'truncated': len(raw) >= MAX_MESSAGE_BYTES,
    }


def fetch_company_mail(phrase='', page=1):
    user = settings.COMPANY_MAIL_USER
    password = settings.COMPANY_MAIL_APP_PASSWORD
    if not user or not password:
        raise CompanyMailError('Email nije povezan. Na serveru postavi COMPANY_MAIL_USER i COMPANY_MAIL_APP_PASSWORD.')
    phrase = ' '.join((phrase or '').split())
    if len(phrase) > 160:
        raise CompanyMailError('Filter fraza može imati najviše 160 znakova.')
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    identity = hashlib.sha256((user + password + settings.COMPANY_MAIL_FOLDER).encode()).hexdigest()
    cache_key = 'company-mail-v1:' + hashlib.sha256(f'{identity}:{phrase}:{page}'.encode()).hexdigest()
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
        if phrase:
            # Literal prevents search injection and supports non-ASCII names.
            client.literal = phrase.encode('utf-8')
            status, data = client.uid('search', 'CHARSET', 'UTF-8', 'SUBJECT')
        else:
            status, data = client.uid('search', None, 'ALL')
        if status != 'OK':
            raise CompanyMailError('Gmail nije izvršio pretragu poruka. Pokušaj ponovo.')
        uids = (data[0] or b'').split()[::-1]
        pages = max(1, (len(uids) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        selected = uids[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        messages = []
        if selected:
            status, rows = client.uid('fetch', b','.join(selected).decode('ascii'),
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
                  'previous_page': page - 1, 'next_page': page + 1,
                  'updated_at': timezone.now()}
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
