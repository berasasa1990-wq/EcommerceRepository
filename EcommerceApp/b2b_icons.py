"""Default category icons; administrators can replace them with uploaded images."""
import unicodedata


def category_icon_name(name):
    name = unicodedata.normalize('NFKD', name.casefold()).encode('ascii', 'ignore').decode()
    for words, icon in [
        (('igl', 'alat'), 'pin'), (('stap', 'prut'), 'rod'), (('masin', 'rol'), 'reel'),
        (('najlon', 'strun', 'konac'), 'line'), (('varalic', 'vobler', 'silikon'), 'lure'),
        (('udic', 'sitni', 'predvez'), 'hook'), (('feeder', 'hranilic'), 'feeder'),
        (('kutij', 'torb', 'ruksak', 'futrol'), 'bag'), (('mam', 'boil', 'pelet', 'hran'), 'bait'),
        (('odjec', 'obuc', 'cizm', 'majic', 'jakn'), 'clothing'),
        (('kamp', 'sator', 'biv', 'krevet', 'stolic'), 'camp'),
    ]:
        if any(word in name for word in words):
            return icon
    return 'gear'
