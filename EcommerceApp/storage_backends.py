"""
Cloudflare R2 storage (S3-compatible).

R2 ne podržava S3 ACL headere — PutObject s ACL-om često daje AccessDenied.
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


try:
    from storages.backends.s3boto3 import S3Boto3Storage as _S3Boto3Storage
except ImportError:  # pragma: no cover
    _S3Boto3Storage = None


if _S3Boto3Storage is not None:

    class CloudflareR2Storage(_S3Boto3Storage):
        """S3Boto3 storage za R2: bez ACL, path-style, custom domain opcionalno."""

        default_acl = None
        querystring_auth = False
        file_overwrite = False
        addressing_style = 'path'
        signature_version = 's3v4'

        def get_object_parameters(self, name):
            params = super().get_object_parameters(name)
            params.pop('ACL', None)
            params.pop('acl', None)
            max_age = getattr(settings, 'MEDIA_CACHE_MAX_AGE', 31536000)
            params.setdefault('CacheControl', f'public, max-age={max_age}, immutable')
            return params

        def _save(self, name, content):
            self.default_acl = None
            return super()._save(name, content)

else:

    class CloudflareR2Storage(FileSystemStorage):
        """
        Fallback ako django-storages nije instaliran.
        Ignoriše S3 kwargs da ne sruši gunicorn na startu.
        """

        def __init__(self, **kwargs):
            allowed = {}
            if 'location' in kwargs:
                allowed['location'] = kwargs['location']
            if 'base_url' in kwargs:
                allowed['base_url'] = kwargs['base_url']
            super().__init__(**allowed)
