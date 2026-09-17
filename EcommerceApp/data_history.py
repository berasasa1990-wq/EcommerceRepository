"""Owner-only browsing of permanent history and unsaved form snapshots."""
import json
import re

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import SavedFormInput, SystemDataRevision, PreservedMediaFile
from .warehouse_access import warehouse_user_required

SENSITIVE = re.compile(r'password|passwd|lozinka|csrf|token|secret|api.?key|card.?number|cvv|cvc', re.I)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_POST
def save_input(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        if str(data.get('owner', request.user.pk)) != str(request.user.pk):
            return JsonResponse({'error': 'Prijavljeni korisnik je promijenjen.'}, status=403)
        event_id = data['event_id']
        if not isinstance(event_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', event_id):
            raise ValueError
        fields = data['payload']
        if not isinstance(fields, list):
            raise ValueError
        safe = []
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError
            name = str(field.get('name', ''))
            if SENSITIVE.search(name) or field.get('type') in ('password', 'file'):
                continue
            safe.append(field)
        path = str(data.get('path', ''))[:2000]
        if not path.startswith('/'):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Neispravan unos.'}, status=400)
    SavedFormInput.objects.get_or_create(owner=request.user, event_id=event_id, defaults={
        'path': path, 'form_key': str(data.get('form_key', ''))[:200], 'payload': safe,
    })
    return JsonResponse({'ok': True})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def history(request):
    from .views_magacin import _magacin_context
    context = _magacin_context(request, section='narudzbe', page_title='Historija podataka')
    inputs = request.GET.get('unos') == '1'
    media = request.GET.get('fajlovi') == '1'
    qs = SavedFormInput.objects.filter(owner=request.user) if inputs else SystemDataRevision.objects.all()
    if media:
        qs = PreservedMediaFile.objects.defer('content').order_by('-pk')
    table = request.GET.get('tabela', '')
    if table and not inputs and not media:
        qs = qs.filter(table_name=table)
    context.update(records=Paginator(qs, 30).get_page(request.GET.get('page')), input_history=inputs, media_history=media, table=table)
    return render(request, 'staff/magacin/data_history.html', context)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def media_download(request, pk):
    import io
    from django.http import FileResponse
    from django.shortcuts import get_object_or_404
    from .models import PreservedMediaFile
    item = get_object_or_404(PreservedMediaFile, pk=pk)
    return FileResponse(io.BytesIO(bytes(item.content)), as_attachment=True, filename=item.name.rsplit('/', 1)[-1])
