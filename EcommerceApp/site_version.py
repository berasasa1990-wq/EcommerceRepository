"""Broj verzije sajta — vidi se u footeru da se zna koji deploy je uživo."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from django.conf import settings


def _read_version_file(root: Path) -> str:
    path = root / 'VERSION'
    try:
        first = path.read_text(encoding='utf-8').splitlines()[0].strip()
    except OSError:
        return ''
    return first


def _git_sha(root: Path) -> str:
    env_sha = (
        os.environ.get('RENDER_GIT_COMMIT')
        or os.environ.get('SOURCE_VERSION')
        or os.environ.get('GIT_COMMIT')
        or ''
    ).strip()
    if env_sha:
        return env_sha[:7]
    try:
        out = subprocess.check_output(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            cwd=root,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.decode('utf-8', errors='ignore').strip()


@lru_cache(maxsize=1)
def build_site_version():
    root = Path(getattr(settings, 'BASE_DIR', Path(__file__).resolve().parents[1]))
    number = (os.environ.get('SITE_VERSION') or '').strip() or _read_version_file(root) or '1'
    sha = _git_sha(root)
    label = f'v{number}'
    if sha:
        label = f'{label} · {sha}'
    return {
        'site_version': number,
        'site_version_sha': sha,
        'site_version_label': label,
    }


def site_version(request):
    return build_site_version()
