"""Canonical customer identifiers shared by model validation."""
import re


def customer_name_key(value):
    return ' '.join((value or '').split()).casefold()


def customer_phone_key(value):
    from .loyalty import ba_mobile_e164
    canonical = ba_mobile_e164(value)
    if canonical:
        return canonical
    digits = re.sub(r'\D', '', value or '')
    return digits[2:] if digits.startswith('00') else digits
