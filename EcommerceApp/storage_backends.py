"""
Cloudflare R2 storage (S3-compatible).

R2 ne podržava S3 ACL headere — PutObject s ACL-om često daje AccessDenied.
"""
from django.conf import settings

try:
    from storages.backends.s3boto3 import S3Boto3Storage
except ImportError:  # pragma: no cover — paket nije instaliran
    from django.core.files.storage import FileSystemStorage as S3Boto3Storage


class CloudflareR2Storage(S3Boto3Storage):
    """S3Boto3 storage podešen za R2: bez ACL, path-style, custom domain opcionalno."""

    default_acl = None
    querystring_auth = False
    file_overwrite = False
    addressing_style = 'path'
    signature_version = 's3v4'

    def get_object_parameters(self, name):
        if not hasattr(super(), 'get_object_parameters'):
            return {}
        params = super().get_object_parameters(name)
        # nikad ne šalji ACL na R2
        params.pop('ACL', None)
        params.pop('acl', None)
        max_age = getattr(settings, 'MEDIA_CACHE_MAX_AGE', 31536000)
        params.setdefault('CacheControl', f'public, max-age={max_age}, immutable')
        return params

    def _save(self, name, content):
        # Osiguraj da parent ne postavi ACL preko default_acl
        if hasattr(self, 'default_acl'):
            self.default_acl = None
        return super()._save(name, content)
