# Stable protocol codes. Registration does not imply implementation or entitlement.
MODULES = {
    'chatbot': ('ChatBot', 'Asistent za kupovinu, preporuke artikala i razgovor s kupcima.'),
    'loyalty': ('Loyalty', 'Kartice kupaca i loyalty popusti.'),
    'article_plus': ('Akcija – Ponuda +', 'Popust na dodatni artikal uz kupovinu prvog.'),
    'quantity': ('Količinski popust', 'Niža cijena pri kupovini više komada.'),
    'bundle': ('Bundle set', 'Zajednička kupovina artikala uz popust na set.'),
    'b2b': ('B2B', 'Poslovni kupci i posebni uslovi.'),
    'wms': ('WMS', 'Napredno skladišno poslovanje.'),
    'analytics': ('Analitika', 'Napredni izvještaji prodaje.'),
    'newsletter': ('Newsletter', 'Kampanje i obavijesti kupcima.'),
    'gift_vouchers': ('Poklon bonovi', 'Digitalni poklon bonovi.'),
    'scratch_cards': ('Grebalice', 'Promotivne nagradne kartice.'),
    'booking': ('Rezervacije', 'Upravljanje terminima i rezervacijama.'),
}
PAID_SALE_MODULES = frozenset({'article_plus', 'quantity', 'bundle'})
IMPLEMENTED = frozenset({'loyalty', 'chatbot', *PAID_SALE_MODULES})
