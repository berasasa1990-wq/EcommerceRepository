"""Repair installations where 0353 was already applied with unqualified SQL."""
from importlib import import_module
from django.db import migrations


def repair(apps, schema_editor):
    install = import_module('EcommerceApp.migrations.0353_search_expression_indexes').install
    install(apps, schema_editor)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('EcommerceApp', '0353_search_expression_indexes')]
    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
