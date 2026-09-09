from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


def validate_banner_link(value):
    if not value:
        return
    if '\\' in value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValidationError('Unesite ispravan link bez razmaka.')
    if value.startswith('/') and not value.startswith('//'):
        return
    URLValidator(schemes=['http', 'https'])(value)
