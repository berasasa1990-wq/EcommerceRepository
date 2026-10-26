from django.db import models
from django.conf import settings


class LicenseState(models.Model):
    installation_id = models.CharField(max_length=200, unique=True)
    document = models.JSONField(default=dict)
    version = models.PositiveBigIntegerField(default=0)
    last_success = models.DateTimeField(null=True, blank=True)
    last_attempt = models.DateTimeField(null=True, blank=True)
    next_attempt = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)
    lease_token = models.CharField(max_length=36, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)


class ActivationRequest(models.Model):
    module = models.CharField(max_length=40)
    requested_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)


class LicenseConnection(models.Model):
    # Singleton; intentionally not registered in Django admin.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    installation_id = models.CharField(max_length=200)
    encrypted_credential = models.TextField(editable=False)
    activated_at = models.DateTimeField(auto_now=True)
