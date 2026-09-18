"""Keep original uploads and files removed/replaced by storage operations."""
import hashlib

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.deprecation import MiddlewareMixin


def preserve_bytes(name, content):
    from .models import PreservedMediaFile
    data = bytes(content)
    PreservedMediaFile.objects.only('pk').get_or_create(
        name=str(name)[:1000], sha256=hashlib.sha256(data).hexdigest(), defaults={'content': data},
    )


class RetainingStorageMixin:
    def _save(self, name, content):
        position = content.tell()
        content.seek(0)
        preserve_bytes(name, content.read())
        content.seek(position)
        if self.exists(name):
            with self.open(name, 'rb') as old:
                previous = old.read()
            preserve_bytes(name, previous)
            # Storage writes are outside DB transactions. Keep a physical version too,
            # so a later DB rollback cannot lose the overwritten original.
            archive_name = '__retained__/' + hashlib.sha256(previous).hexdigest() + '/' + name
            if not self.exists(archive_name):
                super()._save(archive_name, ContentFile(previous))
        return super()._save(name, content)

    def delete(self, name):
        if self.exists(name):
            with self.open(name, 'rb') as old:
                preserve_bytes(name, old.read())
        delete = super().delete
        transaction.on_commit(lambda: delete(name))


class PreserveUploadsMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        # Runs after CSRF's process_view; rejected cross-site uploads are not archived.
        if request.method == 'POST' and request.user.is_authenticated:
            for _, uploads in request.FILES.lists():
                for upload in uploads:
                    position = upload.tell()
                    upload.seek(0)
                    preserve_bytes('original-uploads/' + upload.name, upload.read())
                    upload.seek(position)
        return None
