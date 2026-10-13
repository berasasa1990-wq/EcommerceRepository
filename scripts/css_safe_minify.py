"""Minify CSS and remove earlier byte-identical top-level style rules only.

The final copy remains in its original position, preserving cascade order.
At-rules and their nested rules are retained. Stylesheets using imports or
layers bypass deduplication, because their ordering has additional semantics.
"""
import rcssmin


def minify_css(source):
    css = rcssmin.cssmin(source)
    if '@layer' in css or '@import' in css:
        return css
    rules = []
    start = 0
    depth = 0
    parens = 0
    quote = None
    index = 0
    while index < len(css):
        char = css[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
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
                    rule = css[start:index + 1]
                    if not rule.lstrip().startswith('@'):
                        rules.append((start, index + 1, rule))
                    start = index + 1
            elif char == ';' and depth == 0:
                start = index + 1
        index += 1
    if depth or parens or quote:
        raise ValueError('Unbalanced CSS; refusing to optimize')
    seen = set()
    remove = []
    for start, end, rule in reversed(rules):
        if rule in seen:
            remove.append((start, end))
        seen.add(rule)
    for start, end in remove:
        css = css[:start] + css[end:]
    return css
