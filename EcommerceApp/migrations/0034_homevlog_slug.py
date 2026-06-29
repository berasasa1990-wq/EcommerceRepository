from django.db import migrations, models
from django.utils.text import slugify

TABLE_NAME = 'EcommerceApp_homevlog'
UNIQUE_CONSTRAINT = 'EcommerceApp_homevlog_slug_key'


def populate_vlog_slugs(apps, schema_editor):
    HomeVlog = apps.get_model('EcommerceApp', 'HomeVlog')
    connection = schema_editor.connection
    used_slugs = set()

    with connection.cursor() as cursor:
        if _column_exists(cursor, connection.vendor):
            cursor.execute(
                f'SELECT slug FROM "{TABLE_NAME}" WHERE slug IS NOT NULL AND slug != \'\'',
            )
            used_slugs = {row[0] for row in cursor.fetchall()}

    for vlog in HomeVlog.objects.order_by('id').only('id', 'naslov'):
        with connection.cursor() as cursor:
            current_slug = ''
            if _column_exists(cursor, connection.vendor):
                cursor.execute(
                    f'SELECT slug FROM "{TABLE_NAME}" WHERE id = %s',
                    [vlog.pk],
                )
                row = cursor.fetchone()
                current_slug = row[0] if row and row[0] else ''

            if current_slug:
                used_slugs.add(current_slug)
                continue

            base_slug = slugify(vlog.naslov) or f'vlog-{vlog.pk}'
            slug = base_slug
            counter = 1
            while slug in used_slugs:
                slug = f'{base_slug}-{counter}'
                counter += 1

            cursor.execute(
                f'UPDATE "{TABLE_NAME}" SET slug = %s WHERE id = %s',
                [slug, vlog.pk],
            )
            used_slugs.add(slug)


def _column_exists(cursor, vendor):
    if vendor == 'postgresql':
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = 'slug'
            """,
            [TABLE_NAME],
        )
        return cursor.fetchone() is not None
    if vendor == 'sqlite':
        cursor.execute(f'PRAGMA table_info("{TABLE_NAME}")')
        return any(row[1] == 'slug' for row in cursor.fetchall())
    return False


def _unique_exists(cursor, vendor):
    if vendor == 'postgresql':
        cursor.execute(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = %s
            """,
            [UNIQUE_CONSTRAINT],
        )
        return cursor.fetchone() is not None
    if vendor == 'sqlite':
        cursor.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'index' AND name = %s
            """,
            [UNIQUE_CONSTRAINT],
        )
        return cursor.fetchone() is not None
    return False


def _drop_partial_slug_indexes(cursor, vendor):
    if vendor != 'postgresql':
        return
    cursor.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = %s AND indexname LIKE %s
        """,
        [TABLE_NAME, '%homevlog_slug%'],
    )
    for (index_name,) in cursor.fetchall():
        if index_name == UNIQUE_CONSTRAINT:
            continue
        cursor.execute(f'DROP INDEX IF EXISTS "{index_name}"')


def setup_homevlog_slug(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor

    with connection.cursor() as cursor:
        if not _column_exists(cursor, vendor):
            if vendor == 'postgresql':
                cursor.execute(
                    f'ALTER TABLE "{TABLE_NAME}" ADD COLUMN "slug" varchar(220) NOT NULL DEFAULT \'\'',
                )
                cursor.execute(
                    f'ALTER TABLE "{TABLE_NAME}" ALTER COLUMN "slug" DROP DEFAULT',
                )
            elif vendor == 'sqlite':
                cursor.execute(
                    f'ALTER TABLE "{TABLE_NAME}" ADD COLUMN "slug" varchar(220) NOT NULL DEFAULT \'\'',
                )

    populate_vlog_slugs(apps, schema_editor)

    with connection.cursor() as cursor:
        if _unique_exists(cursor, vendor):
            return

        _drop_partial_slug_indexes(cursor, vendor)

        if vendor == 'postgresql':
            cursor.execute(
                f'ALTER TABLE "{TABLE_NAME}" ADD CONSTRAINT "{UNIQUE_CONSTRAINT}" UNIQUE ("slug")',
            )
        elif vendor == 'sqlite':
            cursor.execute(
                f'CREATE UNIQUE INDEX "{UNIQUE_CONSTRAINT}" ON "{TABLE_NAME}" ("slug")',
            )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('EcommerceApp', '0033_homevlog'),
    ]

    operations = [
        migrations.RunPython(setup_homevlog_slug, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name='homevlog',
                    name='slug',
                    field=models.SlugField(blank=True, max_length=220, unique=True),
                ),
            ],
        ),
    ]