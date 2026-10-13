"""Minify CSS and remove earlier byte-identical top-level style rules only.

The final copy remains in its original position, preserving cascade order.
At-rule order is retained; exact style duplicates within each media/supports
context keep their final cascade position. Stylesheets using imports or
layers bypass deduplication, because their ordering has additional semantics.
"""
import rcssmin


def minify_css(source):
    css = rcssmin.cssmin(source)
    if '@layer' in css or '@import' in css:
        return css
    try:
        from .css_rules import rules
    except ImportError:
        from css_rules import rules

    def deduplicate(group):
        parsed = []
        for rule in rules(group):
            head, separator, body = rule.partition('{')
            if separator and head.startswith(('@media', '@supports')):
                rule = head + '{' + deduplicate(body[:-1]) + '}'
            parsed.append(rule)
        seen = set()
        kept = []
        for rule in reversed(parsed):
            # Do not deduplicate at-rules across contexts or reorder anything.
            if not rule.startswith('@') and '{' in rule:
                if rule in seen:
                    continue
                seen.add(rule)
            kept.append(rule)
        return ''.join(reversed(kept))

    return deduplicate(css)
