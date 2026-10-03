"""Credential-safe, bounded diagnostics for untrusted Mungos responses."""
import json
import re

from .mungos_product import sanitized_json

SENSITIVE = re.compile(r'api[\W_]*key|access[\W_]*code|authorization|credential|password|secret|token|headers', re.I)
TEXT_CREDENTIAL = re.compile(
    r'(?i)(?:x-api-key|ecommerceaccesscode|authorization|credentials?|password|secret|token)'
    r'\s*[:=]\s*(?:"[^"\n]*"|\'[^\'\n]*\'|[^\n,;}]+)'
)


def safe_api_error(body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = str(body)

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                    if not SENSITIVE.search(str(key))}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            return TEXT_CREDENTIAL.sub('[REDACTED]', value)
        return value

    # Decode serialized nested body strings before configured-value redaction.
    return json.loads(sanitized_json(clean(data)))
