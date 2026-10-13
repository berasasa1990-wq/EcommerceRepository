"""Small lossless rule splitter for already minified CSS (no dependency at build time)."""

def rules(css):
    start = 0
    depth = parens = 0
    quote = None
    escaped = False
    for index, char in enumerate(css):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char == '(':
            parens += 1
        elif char == ')':
            parens -= 1
        elif not parens:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    yield css[start:index + 1]
                    start = index + 1
            elif char == ';' and depth == 0:
                yield css[start:index + 1]
                start = index + 1
    if depth or parens or quote:
        raise ValueError('Unbalanced CSS')
    if css[start:].strip():
        yield css[start:]


def first_screen_rules(css, include_products=True):
    """Preserve source order, including media conditions and interactive header states.

    This is an additive critical subset, not unused-CSS deletion. The complete
    original stylesheet stays in its original cascade position after the subset.
    Keep every homepage section/card rule for pages without a hero, too.
    """
    import re
    chrome = re.compile(r'[.#](?:header|nav|logo|promo|site-chrome|mega-|desktop-|mobile-|reference-header|search|wishlist|cart-badge|account-btn|contact-float|scroll-top|hero|slide-|carousel-|home-|bera-seo|category-page|catalog-|product-card|product-image|product-grid|product-stock|product-brand|product-qty|price-|btn|stock-|cat-ico|site-popup|upsell|gratis-offer|ponuda|card-dwell|qty-deal|setb-gate|social-proof)')
    if not include_products:
        chrome = re.compile(r'[.#](?:header|nav|logo|promo|site-chrome|mega-|desktop-|mobile-|reference-header|search|wishlist|cart-badge|account-btn|contact-float|scroll-top|hero|slide-|carousel-|bera-seo|btn)')
    output = []
    for rule in rules(css):
        head, separator, body = rule.partition('{')
        if not separator:
            output.append(rule)
        elif head.startswith(('@media', '@supports')):
            selected = first_screen_rules(body[:-1], include_products)
            if selected:
                output.append(head + '{' + selected + '}')
        elif head.startswith('@'):
            # Keyframes, fonts and unknown at-rules are retained conservatively.
            output.append(rule)
        elif chrome.search(head) or any(selector.strip() in {'html', 'body', 'main', ':root', '*', '*::before', '*::after', 'a', 'img', 'button', 'input', 'svg', 'ul'} for selector in head.split(',')):
            output.append(rule)
    return ''.join(output)
