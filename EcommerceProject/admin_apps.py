from django.contrib.admin.apps import AdminConfig


class SuperuserAdminConfig(AdminConfig):
    default_site = 'EcommerceApp.admin_site.SuperuserAdminSite'
