"""Remove exact repeated style rules without moving their final occurrence."""


def _blocks(css):
    start = 0
    depth = 0
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
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                yield css[start:index + 1]
                start = index + 1
        elif char == ';' and depth == 0:
            yield css[start:index + 1]
            start = index + 1
    if start < len(css):
        yield css[start:]


def deduplicate_rules(css):
    """Input is minified CSS; preserve at-rules and cascade order."""
    blocks = []
    for block in _blocks(css):
        opening = block.find('{')
        if opening >= 0 and block.startswith(('@media ', '@media(', '@supports ', '@supports(', '@container ')):
            block = block[:opening + 1] + deduplicate_rules(block[opening + 1:-1]) + '}'
        blocks.append(block)
    seen = set()
    kept = []
    for block in reversed(blocks):
        # Never deduplicate at-rules, keyframes, or bare declarations.
        if not block.startswith('@') and '{' in block:
            if block in seen:
                continue
            seen.add(block)
        kept.append(block)
    return ''.join(reversed(kept))


def _selectors(prelude):
    """Split selector lists without splitting :is(), :not(), or attributes."""
    start = depth = 0
    quote = None
    escaped = False
    for index, char in enumerate(prelude):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        elif char == ',' and depth == 0:
            yield prelude[start:index]
            start = index + 1
    yield prelude[start:]


def remove_unused_components(css, source_text):
    """Drop retired component scopes only while absent from HTML/JS/Python.

    Use an explicit list, rather than guessing whether dynamic state classes
    are unused. Functional selectors are retained because a missing class in
    :not() or an alternative in :is() does not make a rule unused.
    """
    import re
    retired = (
        'fish-advisor', 'browse-offer', 'catalog-mobile', 'filter-cat',
        'gratis-promo', 'header-akcija', 'header-noviteti', 'header-float',
        'home-image', 'staff-dash', 'staff-email', 'staff-odoo',
        'hero-carousel',
    )
    absent = [name for name in retired if name not in source_text]
    if not absent:
        return css
    required = re.compile(r'\.(' + '|'.join(map(re.escape, absent)) + r')(?:[\w-]*)(?![\w-])')
    result = []
    for block in _blocks(css):
        opening = block.find('{')
        if opening < 0:
            result.append(block)
            continue
        prelude, content = block[:opening], block[opening + 1:-1]
        if prelude.startswith(('@media ', '@media(', '@supports ', '@supports(', '@container ')):
            content = remove_unused_components(content, source_text)
            if content:
                result.append(prelude + '{' + content + '}')
        elif prelude.startswith('@'):
            result.append(block)
        else:
            selectors = [s for s in _selectors(prelude)
                         if any(f in s for f in (':not(', ':is(', ':where(', ':has('))
                         or not required.search(s)]
            if selectors:
                result.append(','.join(selectors) + '{' + content + '}')
    return ''.join(result)


def public_styles(css):
    """Exclude explicitly private/account scopes; never infer unused UI classes.

    Signed-in users and the set builder keep the complete stylesheets.
    Generic rules and rules with complex negation stay intact.
    """
    import re
    private = re.compile(r'\.(?:staff-[\w-]+|account-(?!btn\b)[\w-]+|am-[\w-]+|setb-[\w-]+|cart-exit[\w-]*|cart-abandon[\w-]*)(?![\w-])')
    result = []
    for block in _blocks(css):
        opening = block.find('{')
        if opening < 0:
            result.append(block)
            continue
        prelude, content = block[:opening], block[opening + 1:-1]
        if prelude.startswith(('@media ', '@media(', '@supports ', '@supports(', '@container ')):
            content = public_styles(content)
            if content:
                result.append(prelude + '{' + content + '}')
        elif prelude.startswith('@'):
            result.append(block)
        else:
            selectors = [s for s in _selectors(prelude) if ':not(' in s or ':is(' in s or ':where(' in s or ':has(' in s or not private.search(s)]
            if selectors:
                result.append(','.join(selectors) + '{' + content + '}')
    return ''.join(result)
