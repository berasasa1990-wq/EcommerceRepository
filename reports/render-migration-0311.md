# Render: migration 0311 and history triggers

## Diagnosis and implementation

0311 is an atomic Django migration (the default). Its old implementation dropped the legacy order column, then called install_history() for every application/auth table. PostgreSQL retained the order table DDL lock until the migration committed, while DROP/CREATE TRIGGER acquired additional locks on unrelated tables. Concurrent application transactions acquiring those tables in another order could deadlock. The supplied error establishes the failing operation, but the precise other transaction requires PostgreSQL deadlock logs.

PostgreSQL history uses to_jsonb(OLD/NEW), not a fixed column list. Dropping the legacy column does not require recreating these triggers. 0311 now leaves PostgreSQL triggers untouched and archives nonempty legacy reasons as BASELINE revisions before dropping the column. An absent column is a no-op. Existing audit records are never removed.

SQLite history embeds column names. Only the three known order history triggers are rebuilt, within the migration transaction. Unrelated triggers are retained; an incompatible unrelated trigger causes a safe rollback rather than being deleted. install_history(tables=[table]) rebuilds only order history, not every table.

post_migrate and 0283 also call install_history. On PostgreSQL it now validates and preserves existing enabled history triggers and the existing function. Missing triggers are created without dropping anything. Creation uses transaction.atomic on the correct database alias, transaction-scoped advisory serialization, a recheck under that lock, and a five-second local lock_timeout restored afterward. post_migrate in autocommit commits each missing trigger independently. If already inside an atomic migration, Django retains the enclosing transaction. Invalid or disabled triggers fail explicitly instead of being silently overwritten. Installation traverses tables in sorted order.

Dependencies remain unchanged: 0311 -> 0310; initial history installation 0283 -> 0282. No fake migrations, new migration reset, database reset, or production operation is required by this code change.

## Verified local state and checks

- Local default database is SQLite. 0311 is recorded as applied; the legacy column is absent.
- `python manage.py check`: passes.
- `python manage.py migrate --plan`: no planned operations locally.
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python manage.py test EcommerceApp.tests_retention_trigger_migration`: six passing tests, including PostgreSQL SQL-path regression checks.
- Isolated copy of the existing SQLite database: order count unchanged; legacy reason archived; other tables' trigger definitions unchanged; custom order trigger preserved; history continues after removal; rollback and repeat execution verified.
- No production database connection was used. Local libpq tools exist but there is no PostgreSQL server executable, so real PostgreSQL concurrency testing was not possible.

## Production verification before retrying deploy

Run these read-only queries through an authorized Render database session. Do not assume production matches local SQLite.

```sql
SELECT name, applied
FROM django_migrations
WHERE app = 'EcommerceApp'
  AND name IN ('0283_install_permanent_history', '0310_scratch_reward_coupons',
               '0311_remove_legacy_cancellation_reason')
ORDER BY name;

SELECT table_schema, column_name, is_nullable
FROM information_schema.columns
WHERE table_name = 'EcommerceApp_order'
  AND column_name = 'razlog_otkazivanja';

SELECT n.nspname AS schema_name, c.relname AS table_name,
       t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal AND t.tgname LIKE 'retain_%'
ORDER BY n.nspname, c.relname, t.tgname;

SELECT pg_get_functiondef(to_regprocedure('ecommerce_preserve_row()'));

SELECT COUNT(*) AS history_rows FROM "EcommerceApp_systemdatarevision";
SELECT COUNT(*) AS order_rows FROM "EcommerceApp_order";
```

Interpretation:

- No 0311 record + column present: expected after rollback of the reported atomic PostgreSQL migration. Retry normal migrate with the corrected source; it archives remaining reasons and removes the column without reinstalling PostgreSQL triggers.
- No 0311 record + column absent: corrected migration is a no-op and normal migrate records its completion.
- 0311 recorded + column absent: normal migrate skips it; the corrected post_migrate handler preserves existing PostgreSQL triggers.
- 0311 recorded + column present, disabled/mismatched triggers, or missing history function with otherwise inconsistent definitions: investigate first. Do not fake migrations or drop triggers. Provide actual catalog results before choosing a targeted follow-up migration.

Deploy only one migration runner at a time. Normal ALTER TABLE still requires an order-table lock; long-running production transactions can delay it. If lock contention persists after this fix, collect PostgreSQL deadlock/lock logs and inspect pg_stat_activity/pg_locks. A short maintenance window can reduce contention; do not terminate production transactions without reviewing their work.

The code removes the identified cross-table trigger-reinstallation deadlock path. Successful Render deployment remains unconfirmed until it runs against the actual production state. No git push or production migration was executed.
