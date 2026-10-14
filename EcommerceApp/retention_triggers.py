"""Database-level history, covering QuerySet.update/delete and raw SQL too."""
import hashlib

AUDIT_TABLE = 'EcommerceApp_systemdatarevision'
EXCLUDED = {AUDIT_TABLE.lower(), 'ecommerceapp_manualorderdraftrevision', 'ecommerceapp_savedforminput', 'ecommerceapp_preservedmediafile'}


def install_history(connection, baseline=False):
    if connection.vendor not in ('sqlite', 'postgresql'):
        raise RuntimeError('Permanent history requires SQLite or PostgreSQL.')
    quote = connection.ops.quote_name
    audit = quote(AUDIT_TABLE)
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        if AUDIT_TABLE not in tables:
            return
        if connection.vendor == 'postgresql':
            cursor.execute(f'''
                CREATE OR REPLACE FUNCTION ecommerce_preserve_row() RETURNS trigger AS $$
                BEGIN
                    IF TG_OP = 'UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN RETURN NEW; END IF;
                    INSERT INTO {audit} (table_name, record_key, operation, "before", "after", created_at)
                    VALUES (TG_TABLE_NAME,
                        COALESCE(to_jsonb(NEW)->>TG_ARGV[0], to_jsonb(OLD)->>TG_ARGV[0], ''),
                        TG_OP,
                        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
                        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
                        CURRENT_TIMESTAMP);
                    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            ''')
        for table in tables:
            if (not table.lower().startswith(('ecommerceapp_', 'auth_')) and table != 'django_admin_log') or table.lower() in EXCLUDED:
                continue
            columns = connection.introspection.get_table_description(cursor, table)
            names = [col.name for col in columns]
            constraints = connection.introspection.get_constraints(cursor, table)
            pk = next((item['columns'][0] for item in constraints.values() if item.get('primary_key')), names[0])
            digest = hashlib.sha256(table.encode()).hexdigest()[:16]
            quoted = quote(table)
            literal = "'" + table.replace("'", "''") + "'"
            if connection.vendor == 'postgresql':
                trigger = quote('retain_' + digest)
                cursor.execute(f'DROP TRIGGER IF EXISTS {trigger} ON {quoted}')
                cursor.execute(f'''CREATE TRIGGER {trigger} AFTER INSERT OR UPDATE OR DELETE ON {quoted}
                    FOR EACH ROW EXECUTE FUNCTION ecommerce_preserve_row('{pk.replace("'", "''")}')''')
                if baseline:
                    cursor.execute(f'''INSERT INTO {audit} (table_name, record_key, operation, "before", "after", created_at)
                        SELECT {literal}, CAST(t.{quote(pk)} AS text), 'BASELINE', NULL, to_jsonb(t), CURRENT_TIMESTAMP
                        FROM {quoted} t WHERE NOT EXISTS (
                            SELECT 1 FROM {audit} h WHERE h.table_name = {literal}
                            AND h.record_key = CAST(t.{quote(pk)} AS text)
                        )''')
            else:
                def row_json(prefix):
                    chunks = []
                    for start in range(0, len(names), 40):
                        pairs = []
                        for name in names[start:start + 40]:
                            pairs.extend(["'" + name.replace("'", "''") + "'", prefix + '.' + quote(name)])
                        chunks.append('json_object(' + ','.join(pairs) + ')')
                    # json_patch removes null-valued keys; use json_set to preserve them.
                    result = chunks[0]
                    for start in range(40, len(names), 40):
                        args = []
                        for name in names[start:start + 40]:
                            args.extend(["'$.\"" + name.replace("'", "''") + "\"'", prefix + '.' + quote(name)])
                        result = 'json_set(' + result + ',' + ','.join(args) + ')'
                    return result
                for operation in ('INSERT', 'UPDATE', 'DELETE'):
                    trigger = quote('retain_' + digest + '_' + operation.lower())
                    cursor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
                    old = row_json('OLD') if operation != 'INSERT' else 'NULL'
                    new = row_json('NEW') if operation != 'DELETE' else 'NULL'
                    source = 'OLD' if operation == 'DELETE' else 'NEW'
                    when = f'WHEN {old} IS NOT {new}' if operation == 'UPDATE' else ''
                    cursor.execute(f'''CREATE TRIGGER {trigger} AFTER {operation} ON {quoted} {when}
                        BEGIN INSERT INTO {audit} (table_name, record_key, operation, "before", "after", created_at)
                        VALUES ({literal}, CAST({source}.{quote(pk)} AS TEXT), '{operation}', {old}, {new},
                        STRFTIME('%Y-%m-%d %H:%M:%f', 'now')); END''')
                if baseline:
                    cursor.execute(f'''INSERT INTO {audit} (table_name, record_key, operation, "before", "after", created_at)
                        SELECT {literal}, CAST(t.{quote(pk)} AS TEXT), 'BASELINE', NULL, {row_json('t')},
                        STRFTIME('%Y-%m-%d %H:%M:%f', 'now') FROM {quoted} t
                        WHERE NOT EXISTS (SELECT 1 FROM {audit} h WHERE h.table_name = {literal}
                            AND h.record_key = CAST(t.{quote(pk)} AS TEXT))''')

    install_history_guards(connection)


def refresh_history_triggers(sender, using, **kwargs):
    from django.db import connections
    if sender.name == 'EcommerceApp':
        install_history(connections[using])


def install_history_guards(connection):
    """Keep committed history append-only, even for direct SQL."""
    quote = connection.ops.quote_name
    audit = quote(AUDIT_TABLE)
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        if AUDIT_TABLE not in tables:
            return
        if connection.vendor == 'sqlite':
            for operation in ('UPDATE', 'DELETE'):
                name = quote('protect_system_history_' + operation.lower())
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')
                cursor.execute(f"""CREATE TRIGGER {name} BEFORE {operation} ON {audit}
                    BEGIN SELECT RAISE(ABORT, 'Permanent history cannot be modified or deleted'); END""")
        elif connection.vendor == 'postgresql':
            cursor.execute("""CREATE OR REPLACE FUNCTION ecommerce_block_history_loss()
                RETURNS trigger AS $$ BEGIN
                RAISE EXCEPTION 'Permanent history cannot be modified, deleted or truncated';
                END; $$ LANGUAGE plpgsql;""")
            cursor.execute(f'DROP TRIGGER IF EXISTS protect_system_history ON {audit}')
            cursor.execute(f"""CREATE TRIGGER protect_system_history BEFORE UPDATE OR DELETE ON {audit}
                FOR EACH ROW EXECUTE FUNCTION ecommerce_block_history_loss()""")
            # TRUNCATE bypasses row-level history triggers. Refuse it on tracked tables.
            for table in tables:
                if table.lower() in EXCLUDED and table != AUDIT_TABLE:
                    continue
                if not table.lower().startswith(('ecommerceapp_', 'auth_')) and table != 'django_admin_log':
                    continue
                quoted = quote(table)
                cursor.execute(f'DROP TRIGGER IF EXISTS protect_history_truncate ON {quoted}')
                cursor.execute(f"""CREATE TRIGGER protect_history_truncate BEFORE TRUNCATE ON {quoted}
                    FOR EACH STATEMENT EXECUTE FUNCTION ecommerce_block_history_loss()""")
        else:
            raise RuntimeError('Permanent history requires SQLite or PostgreSQL.')
