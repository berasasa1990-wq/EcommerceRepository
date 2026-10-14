"""Database-level history, covering QuerySet.update/delete and raw SQL too."""
import hashlib

from django.db import transaction

AUDIT_TABLE = 'EcommerceApp_systemdatarevision'
EXCLUDED = {AUDIT_TABLE.lower(), 'ecommerceapp_manualorderdraftrevision', 'ecommerceapp_savedforminput', 'ecommerceapp_preservedmediafile'}


def install_history(connection, baseline=False, tables=None):
    if connection.vendor not in ('sqlite', 'postgresql'):
        raise RuntimeError('Permanent history requires SQLite or PostgreSQL.')
    quote = connection.ops.quote_name
    audit = quote(AUDIT_TABLE)
    with connection.cursor() as cursor:
        available = connection.introspection.table_names(cursor)
        selected = sorted(available if tables is None else set(tables) & set(available))
        if AUDIT_TABLE not in available:
            return
        if connection.vendor == 'postgresql':
            _ensure_postgres_function(connection, audit)
        for table in selected:
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
                _ensure_postgres_trigger(connection, table, pk, 'retain_' + digest)
                if baseline:
                    cursor.execute(f'''INSERT INTO {audit} (table_name, record_key, operation, "before", "after", created_at)
                        SELECT {literal}, CAST(t.{quote(pk)} AS text), 'BASELINE', NULL, to_jsonb(t), CURRENT_TIMESTAMP
                        FROM {quoted} t''')
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
                        STRFTIME('%Y-%m-%d %H:%M:%f', 'now') FROM {quoted} t''')



def _trigger_definition(cursor, table, trigger):
    cursor.execute("""
        SELECT t.tgtype, t.tgenabled, p.proname, t.tgargs, t.tgnargs
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(%s) AND t.tgname = %s AND NOT t.tgisinternal
    """, [table, trigger])
    return cursor.fetchone()


def _validate_postgres_trigger(row, table, pk):
    if row is None:
        return False
    event_type, enabled, function, arguments, count = row
    if (event_type != 29 or enabled not in ('O', 'A')
            or function != 'ecommerce_preserve_row' or count != 1
            or bytes(arguments) != (pk + '\0').encode()):
        raise RuntimeError(f'Unexpected history trigger on {table}; inspect it before changing it.')
    return True


def _ensure_postgres_trigger(connection, table, pk, trigger):
    quoted = connection.ops.quote_name(table)
    with connection.cursor() as cursor:
        if _validate_postgres_trigger(_trigger_definition(cursor, quoted, trigger), table, pk):
            return  # No table DDL or table locks for an existing correct trigger.
    # post_migrate runs in autocommit: commit each new trigger separately.
    # An enclosing migration retains its own atomic transaction semantics.
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute('SHOW lock_timeout')
            previous_timeout = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('lock_timeout', '5s', true)")
            # Serialize concurrent installers; recheck after acquiring the lock.
            lock_key = int.from_bytes(hashlib.sha256(('history:' + table).encode()).digest()[:8], 'big', signed=True)
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])
            if not _validate_postgres_trigger(_trigger_definition(cursor, quoted, trigger), table, pk):
                cursor.execute(f"CREATE TRIGGER {connection.ops.quote_name(trigger)} "
                               f"AFTER INSERT OR UPDATE OR DELETE ON {quoted} "
                               "FOR EACH ROW EXECUTE FUNCTION ecommerce_preserve_row("
                               + "'" + pk.replace("'", "''") + "')")
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [previous_timeout])


def _ensure_postgres_function(connection, audit):
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regprocedure('ecommerce_preserve_row()')")
        if cursor.fetchone()[0] is not None:
            return
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute('SHOW lock_timeout')
            previous_timeout = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('lock_timeout', '5s', true)")
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [2830283])
            cursor.execute("SELECT to_regprocedure('ecommerce_preserve_row()')")
            if cursor.fetchone()[0] is None:
                cursor.execute(f'''
                CREATE FUNCTION ecommerce_preserve_row() RETURNS trigger AS $$
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
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [previous_timeout])

def refresh_history_triggers(sender, using, **kwargs):
    from django.db import connections
    if sender.name == 'EcommerceApp':
        install_history(connections[using])
