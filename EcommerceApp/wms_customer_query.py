"""Page the merged customer directory in SQL, without loading order models."""
import math
from django.db import connection
from django.db.models import CharField, F, Value, Window
from django.db.models.functions import Cast, RowNumber
from .models import WMSCustomer, Order, WMSOrder


def customer_page(query, requested_page):
    sources = [
        (WMSCustomer.objects.filter(is_deleted=False), 'ime_prezime', 0, [F('ime_prezime').asc(), F('pk').asc()]),
        (Order.objects.all(), 'ime_prezime', 1, [F('kreirana').desc(), F('pk').desc()]),
        (WMSOrder.objects.filter(source_order__isnull=True, customer__isnull=True), 'kupac', 2, [F('kreirana').desc(), F('pk').desc()]),
    ]
    from django.db.models import Q
    parts, params = [], []
    for queryset, name, priority, ordering in sources:
        if query:
            queryset = queryset.filter(Q(**{name+'__icontains': query}) | Q(telefon__icontains=query) | Q(grad__icontains=query))
        fields = dict(_id=Cast('pk', CharField()) if priority == 0 else Value('', output_field=CharField()),
                      _name=F(name), _phone=F('telefon'), _address=F('adresa'), _city=F('grad'),
                      _postal=F('postanski_broj'), _email=Value('', output_field=CharField()) if priority == 0 else F('email'),
                      _priority=Value(priority), _position=Window(RowNumber(), order_by=ordering))
        sql, values = queryset.order_by().annotate(**fields).values(*fields).query.sql_with_params()
        parts.append(sql); params.extend(values)
    quote = connection.ops.quote_name
    if connection.vendor == 'sqlite':
        connection.ensure_connection()
        connection.connection.create_function('wms_name_key', 1, lambda s: (s or '').strip().lower(), deterministic=True)
        connection.connection.create_function('wms_phone_key', 1, lambda s: (s or '').strip(), deterministic=True)
        norm_name = lambda col: f'wms_name_key({col})'
        norm_phone = lambda col: f'wms_phone_key({col})'
        connection.connection.create_function('wms_sort_name', 1, lambda s: (s or '').lower(), deterministic=True)
        sort = 'wms_sort_name(_name) COLLATE BINARY'
    else:
        # Match Python strip(), including tabs/newlines and Unicode whitespace.
        whitespace = ''.join(chr(i) for i in range(0x3100) if chr(i).isspace())
        # Literal is generated from a fixed character set, never request input.
        literal = "'" + whitespace.replace("'", "''") + "'"
        norm_name = lambda col: f'LOWER(BTRIM({col}, {literal}))'
        norm_phone = lambda col: f'BTRIM({col}, {literal})'
        sort = 'LOWER(_name) COLLATE "C"'
    cte = f'''WITH candidates AS ({' UNION ALL '.join(parts)}), ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY {norm_name('_name')}, {norm_phone('_phone')}
            ORDER BY _priority, _position) AS winner FROM candidates
        ), deleted AS MATERIALIZED (
            SELECT {norm_name('ime_prezime')} AS name_key, {norm_phone('telefon')} AS phone_key
            FROM {quote(WMSCustomer._meta.db_table)} WHERE is_deleted = %s
        ), directory AS (
        SELECT * FROM ranked r WHERE winner = 1 AND NOT EXISTS (
            SELECT 1 FROM deleted d WHERE d.name_key = {norm_name('r._name')}
            AND d.phone_key = {norm_phone('r._phone')})
        )'''
    params.append(True)
    try: page = int(requested_page)
    except (TypeError, ValueError): page = 1
    select = cte + f' SELECT _id,_name,_phone,_address,_city,_postal,_email,_priority,COUNT(*) OVER () FROM directory ORDER BY {sort}, _priority, _position LIMIT %s OFFSET %s'
    with connection.cursor() as cursor:
        cursor.execute(select, [*params, 30, (max(1, page)-1)*30])
        fetched = cursor.fetchall()
        if fetched and page > 0:
            count = fetched[0][-1]
            pages = max(1, math.ceil(count/30))
        elif not fetched and page == 1:
            pages = 1
        else:
            cursor.execute(cte+' SELECT COUNT(*) FROM directory', params)
            count = cursor.fetchone()[0]
            pages = max(1, math.ceil(count/30))
            page = pages
            cursor.execute(select, [*params, 30, (page-1)*30])
            fetched = cursor.fetchall()
        customers = []
        for id_, name, phone, address, city, postal, email, priority in (row[:-1] for row in fetched):
            row = dict(id=int(id_) if id_ else '', name=name, phone=phone, address=address, city=city, postal=postal)
            if priority: row['email'] = email
            customers.append(row)
    return {'customers': customers, 'page': page, 'pages': pages}
