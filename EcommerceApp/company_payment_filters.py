"""Display filters and aliases used by the offline statement importer."""
PAYMENT_FILTERS = (
    ('Damjanovic', 'Damjanovic'),
    ('ajla', 'ajla'), ('bhstar', 'bhstar'), ('bh star', 'bhstar'),
    ('ribarska pri', 'ribarska pri'), ('ribolovacka pri', 'ribolovacka pri'),
    ('preprelica', 'preprelica'), ('lovac', 'lovac'),
    ('pro carp', 'pro carp'), ('procarp', 'pro carp'), ('bistro', 'bistro'),
    ('velic', 'velic'), ('beton', 'beton'), ('limited', 'limited'),
    ('lim.it.ed', 'limited'), ('zlatna ribica', 'zlatna ribica'),
    ('zlatna udica', 'zlatna udica'), ('default', 'default'),
    ('fishing store', 'fishing store'),
)
FILTER_PAYERS = dict(PAYMENT_FILTERS)

# Only these payer-name phrases are visible in Duguje/Potražuje.
DISPLAY_PAYER_PHRASES = (
    'DEFAULT', 'ORHIDEJA', 'BISTRO BRCKO', 'FISHING STORE', 'GROM',
    'LIM.IT.EDIL', 'RIBARSKA PRICA', 'RIBOLOVACKA PRICA', 'TERRA SANA',
    'TR CVJECARA PET SHOP LEO', 'KASUMOVIC ENES', 'TRGOVACKA RADNJA BISTRO',
    'TRGOVINA KUM Dejan Bilic', 'TRGOVINJSKA RADNJA AJLA',
    'TRGOVINSKA RADNJA SAVA RIVER', 'TROFEJ DOO LAKTASI', 'VELIC BETON',
    'ZLATNA RIBICA', 'DAMJANOVIC PROMEX DOO', 'BHSTAR', 'BALKAN UNIT LUI',
    'AJDARED DOO SREBRENIK', 'ALMY',
)


def display_payer_allowed(name):
    return display_payer_name(name) is not None


def display_payer_name(name):
    """Use the configured company name instead of statement address suffixes."""
    from .company_statements import statement_normalize
    normalized = statement_normalize(name or '')
    for phrase in sorted(DISPLAY_PAYER_PHRASES, key=len, reverse=True):
        if statement_normalize(phrase) in normalized:
            return phrase
    return None
