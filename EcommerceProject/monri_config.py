"""Monri settings: credentials from process environment only; flags may use dotenv."""


def read_monri_config(environment, dotenv):
    def value(name, default=''):
        return environment.get(name, dotenv.get(name, default))

    enabled = str(value('MONRI_ENABLED', 'True')).strip().lower() in ('true', '1', 'yes')
    mode = str(value('MONRI_ENVIRONMENT', 'test')).strip().lower()
    return {
        'MONRI_ENABLED': enabled and mode in ('test', 'production'),
        'MONRI_ENVIRONMENT': mode,
        'MONRI_MERCHANT_KEY': environment.get('MONRI_MERCHANT_KEY', ''),
        'MONRI_AUTHENTICITY_TOKEN': environment.get('MONRI_AUTHENTICITY_TOKEN', ''),
        'MONRI_PUBLIC_BASE_URL': str(value('MONRI_PUBLIC_BASE_URL', 'https://carpologijabh.ba')).strip().rstrip('/'),
    }
