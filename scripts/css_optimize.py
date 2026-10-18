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
