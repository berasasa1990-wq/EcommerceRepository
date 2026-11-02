"""Aggregate our opt-in JSONL events, never raw access log request lines."""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Top routes from HTTP diagnostics JSONL (application estimates, not Render network bytes).'

    def add_arguments(self, parser):
        parser.add_argument('logfile')
        parser.add_argument('--hours', type=float, default=24)
        parser.add_argument('--top', type=int, default=20)
        parser.add_argument('--until', help='UTC ISO timestamp; default: now. Allows reproducible windows.')

    def handle(self, *args, **options):
        try:
            until = datetime.fromisoformat(options['until'].replace('Z', '+00:00')) if options['until'] else datetime.now(timezone.utc)
            if until.tzinfo is None or options['hours'] <= 0 or options['top'] <= 0:
                raise ValueError
        except ValueError:
            raise CommandError('Use a timezone-aware --until, positive --hours and --top.')
        since = until - timedelta(hours=options['hours'])
        groups = defaultdict(lambda: {'requests': 0, 'body_bytes': 0, 'known_sizes': 0,
            'unknown_sizes': 0, 'partial_streams': 0, 'file_length_estimates': 0,
            'statuses': defaultdict(int), 'ua_categories': defaultdict(int)})
        rates = set()
        ignored = 0
        try:
            stream = open(options['logfile'], encoding='utf-8')
        except OSError:
            raise CommandError('Cannot open diagnostics file.')
        with stream:
            for line in stream:
                # Render exports can prepend timestamps before the JSON message.
                try:
                    row = json.loads(line[line.index('{'):])
                    if row.get('event') != 'http_bandwidth' or row.get('schema_version') != 1:
                        continue
                    stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
                    if not since <= stamp <= until:
                        continue
                    size = row['body_bytes']
                    if size is not None and (type(size) is not int or size < 0):
                        raise ValueError
                    key = (row['host'], row['route'])
                    status = str(int(row['status']))
                    ua = row['ua_category']
                    rate = float(row['sample_rate'])
                    if not all(isinstance(v, str) for v in (*key, ua)):
                        raise ValueError
                except (ValueError, KeyError, TypeError, AttributeError):
                    ignored += 1
                    continue
                group = groups[key]
                group['requests'] += 1
                group['statuses'][status] += 1
                group['ua_categories'][ua] += 1
                if size is None:
                    group['unknown_sizes'] += 1
                else:
                    group['known_sizes'] += 1
                    group['body_bytes'] += size
                group['partial_streams'] += row.get('complete') is False
                group['file_length_estimates'] += row.get('byte_source') == 'file_content_length'
                rates.add(rate)
        rows = [dict(host=host, route=route, **group,
            average_body_bytes=round(group['body_bytes'] / group['known_sizes'], 2) if group['known_sizes'] else None)
            for (host, route), group in groups.items()]
        top = options['top']
        result = {
            'note': 'Observed application body estimates only; excludes headers/TLS/network overhead. File lengths are unverified. Sampled counts are not extrapolated. Not Render billed bytes.',
            'since': since.isoformat(), 'until': until.isoformat(), 'sample_rates': sorted(rates),
            'ignored_lines': ignored, 'observed_requests': sum(r['requests'] for r in rows),
            'observed_body_bytes': sum(r['body_bytes'] for r in rows),
            'top_by_bytes': sorted(rows, key=lambda r: r['body_bytes'], reverse=True)[:top],
            'top_by_requests': sorted(rows, key=lambda r: r['requests'], reverse=True)[:top],
            'top_by_average': sorted(rows, key=lambda r: r['average_body_bytes'] if r['average_body_bytes'] is not None else -1, reverse=True)[:top],
        }
        self.stdout.write(json.dumps(result, indent=2))
