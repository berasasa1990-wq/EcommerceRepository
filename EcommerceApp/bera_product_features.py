"""Extract short feature statements already present in a product description."""
import html
import re

from django.utils.html import strip_tags

_FEATURE_WORDS = re.compile(
    r'\b(?:materijal\w*|udob\w*|lagan\w*|prozrač\w*|prozrac\w*|'
    r'dizajn\w*|pogod\w*|vodootpor\w*|otpor\w*|izdrž\w*|izdrz\w*|'
    r'procesor\w*|memorij\w*|ram|ssd|hdd|ekran\w*|displej\w*|'
    r'baterij\w*|kamer\w*|rezolucij\w*|kapacitet\w*|dimenzij\w*|'
    r'težin\w*|tezin\w*|dužin\w*|duzin\w*|snag\w*|bluetooth|wi-?fi|'
    r'usb\w*|hdmi|kompatibil\w*|ventilacij\w*|amortizacij\w*)\b',
    re.IGNORECASE,
)


def extract_product_features(description, limit=4):
    """Return verbatim statements; never infer specifications or benefits."""
    if not description or re.search(r'<(?:ul|ol)\b', description, re.IGNORECASE):
        # Existing lists already receive the blue checkmark styling.
        return []
    text = re.sub(r'<(?:br\s*/?|/p|/div|/h[1-6])\s*>', '\n', description, flags=re.IGNORECASE)
    text = html.unescape(strip_tags(text))
    features = []
    seen = set()
    for fragment in re.split(r'[\r\n]+|(?<=[.!?])\s+(?=[A-ZČĆŽŠĐ])', text):
        fragment = fragment.strip()
        explicit_bullet = bool(re.match(r'^(?:[-•✓✔*]|\d+[.)])\s+', fragment))
        fragment = re.sub(r'^(?:[-•✓✔*]|\d+[.)])\s+', '', fragment)
        fragment = re.sub(r'\s+', ' ', fragment).strip()
        if not 4 <= len(fragment) <= 160:
            continue
        if not (explicit_bullet or _FEATURE_WORDS.search(fragment)):
            continue
        key = fragment.casefold()
        if key in seen:
            continue
        features.append(fragment)
        seen.add(key)
        if len(features) >= limit:
            break
    return features
