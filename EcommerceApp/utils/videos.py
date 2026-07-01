import json
import struct
import subprocess
from pathlib import Path

from django.core.exceptions import ValidationError

BANNER_VIDEO_MAX_SECONDS = 6
BANNER_VIDEO_MAX_BYTES = 20 * 1024 * 1024
BANNER_VIDEO_ALLOWED_EXTENSIONS = {'.mp4', '.webm', '.mov', '.m4v'}


def _ffprobe_duration_seconds(path):
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'json',
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        payload = json.loads(result.stdout)
        return float(payload['format']['duration'])
    except (FileNotFoundError, subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _mp4_duration_seconds(path):
    with open(path, 'rb') as handle:
        while True:
            header = handle.read(8)
            if len(header) < 8:
                return None
            size, atom_type = struct.unpack('>I4s', header)
            if size < 8:
                return None
            if atom_type == b'moov':
                moov_data = handle.read(size - 8)
                idx = moov_data.find(b'mvhd')
                if idx == -1:
                    return None
                mvhd = moov_data[idx + 8:]
                if len(mvhd) < 20:
                    return None
                version = mvhd[0]
                if version == 0:
                    timescale = struct.unpack('>I', mvhd[12:16])[0]
                    duration = struct.unpack('>I', mvhd[16:20])[0]
                else:
                    if len(mvhd) < 32:
                        return None
                    timescale = struct.unpack('>I', mvhd[20:24])[0]
                    duration = struct.unpack('>Q', mvhd[24:32])[0]
                if not timescale:
                    return None
                return duration / timescale
            handle.seek(size - 8, 1)


def _video_file_path(uploaded_file):
    if hasattr(uploaded_file, 'temporary_file_path'):
        try:
            return Path(uploaded_file.temporary_file_path())
        except (NotImplementedError, AttributeError):
            pass
    if getattr(uploaded_file, 'path', None):
        return Path(uploaded_file.path)
    return None


def video_duration_seconds(uploaded_file):
    path = _video_file_path(uploaded_file)
    if path is None or not path.exists():
        return None

    duration = _ffprobe_duration_seconds(path)
    if duration is None:
        duration = _mp4_duration_seconds(path)
    return duration


def validate_banner_video(uploaded_file):
    if not uploaded_file:
        return

    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in BANNER_VIDEO_ALLOWED_EXTENSIONS:
        allowed = ', '.join(sorted(BANNER_VIDEO_ALLOWED_EXTENSIONS))
        raise ValidationError(f'Video mora biti u formatu: {allowed}.')

    size = getattr(uploaded_file, 'size', None)
    if size and size > BANNER_VIDEO_MAX_BYTES:
        raise ValidationError('Video je prevelik. Maksimalna veličina je 20 MB.')

    duration = video_duration_seconds(uploaded_file)
    if duration is None:
        raise ValidationError(
            'Trajanje videa nije moguće provjeriti. Koristite MP4 (preporučeno) ili instalirajte ffprobe na serveru.',
        )
    if duration > BANNER_VIDEO_MAX_SECONDS + 0.05:
        raise ValidationError(
            f'Video može trajati najviše {BANNER_VIDEO_MAX_SECONDS} sekundi (trenutno: {duration:.1f} s).',
        )