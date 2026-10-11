from django.contrib.admin.apps import AdminConfig


class BeraAdminConfig(AdminConfig):
    default_site = 'EcommerceApp.admin_site.BeraAdminSite'
