(function () {
  'use strict';
  function initLookups(root) {
    root.querySelectorAll('select[data-settings-lookup]').forEach(function (select) {
      if (select.dataset.lookupReady) return;
      select.dataset.lookupReady = '1';
      var input = document.createElement('input');
      input.type = 'search';
      input.placeholder = 'Pretraži po nazivu ili šifri';
      input.setAttribute('aria-label', 'Pretraga za ' + (select.labels[0] ? select.labels[0].textContent : 'odabir'));
      input.className = 'settings-lookup';
      select.before(input);
      var status = document.createElement('span');
      status.className = 'settings-help';
      status.setAttribute('role', 'status');
      select.after(status);
      var timer, controller;
      input.addEventListener('input', function () {
        clearTimeout(timer);
        if (controller) controller.abort();
        timer = setTimeout(function () {
          controller = new AbortController();
          var url = new URL(select.dataset.lookupUrl, window.location.origin);
          url.searchParams.set('model', select.dataset.settingsLookup);
          url.searchParams.set('q', input.value);
          fetch(url, {credentials: 'same-origin', signal: controller.signal, headers: {'Accept': 'application/json'}})
            .then(function (response) { if (!response.ok) throw new Error('search'); return response.json(); })
            .then(function (data) {
              var selected = select.value;
              var current = select.selectedOptions[0];
              select.replaceChildren(new Option('---------', ''));
              if (selected && current) select.add(new Option(current.textContent, selected, true, true));
              data.results.forEach(function (row) {
                if (String(row.id) !== selected) select.add(new Option(row.text, row.id));
              });
              select.value = selected;
              status.textContent = data.results.length ? 'Odaberite rezultat iz liste.' : 'Nema rezultata.';
            }).catch(function (error) { if (error.name !== 'AbortError') status.textContent = 'Pretraga nije uspjela. Pokušajte ponovo.'; });
        }, 250);
      });
    });
  }
  document.querySelectorAll('[data-formset-prefix]').forEach(function (section) {
    section.querySelector('[data-add-form]').addEventListener('click', function () {
      var total = section.querySelector('[name="' + section.dataset.formsetPrefix + '-TOTAL_FORMS"]');
      var count = Number(total.value);
      var template = section.querySelector('[data-formset-empty]');
      var holder = document.createElement('div');
      holder.innerHTML = template.innerHTML.replace(/__prefix__/g, String(count));
      var row = holder.firstElementChild;
      section.querySelector('[data-formset-rows]').appendChild(row);
      total.value = count + 1;
      initLookups(row);
      row.scrollIntoView({behavior: 'smooth', block: 'center'});
    });
  });
  initLookups(document);
  var form = document.getElementById('panelSettingsForm');
  var logo = form.querySelector('input[type="file"][name="logo"]');
  if (logo) {
    var logoStatus = document.createElement('div');
    logoStatus.className = 'settings-help';
    logoStatus.setAttribute('role', 'status');
    logo.after(logoStatus);
    logo.addEventListener('change', async function () {
      if (!logo.files.length) return;
      var upload = new FormData();
      upload.append('logo', logo.files[0]);
      upload.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]').value);
      var buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
      buttons.forEach(function (button) { button.disabled = true; });
      logo.disabled = true;
      logoStatus.textContent = 'Logo se čuva…';
      try {
        var response = await fetch(form.dataset.logoUploadUrl, {method: 'POST', body: upload, credentials: 'same-origin', signal: AbortSignal.timeout(20000)});
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Prijavite se ponovo i pokušajte upload.');
        var result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Upload nije uspio.');
        var container = logo.closest('.settings-field');
        var preview = container.querySelector('.settings-image-preview');
        if (!preview) {
          preview = document.createElement('img');
          preview.className = 'settings-image-preview';
          preview.alt = 'Logo sajta';
          logoStatus.after(preview);
        }
        preview.src = result.url;
        var clear = container.querySelector('[name="logo-clear"]');
        if (clear) clear.checked = false;
        logo.value = '';
        logoStatus.textContent = result.message;
      } catch (error) {
        logoStatus.textContent = error.message || 'Upload nije uspio. Pokušajte ponovo.';
      } finally {
        logo.disabled = false;
        buttons.forEach(function (button) { button.disabled = false; });
      }
    });
  }
  var dirty = false;
  form.addEventListener('change', function () { dirty = true; });
  var saving = false;
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    var status = document.getElementById('settingsSaveStatus');
    var buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
    buttons.forEach(function (button) { button.disabled = true; });
    status.hidden = false;
    status.className = 'settings-help';
    status.textContent = 'Podešavanja se čuvaju…';
    try {
      var payload = new FormData(form);
      // Explicitly include management and row inputs even if the browser has
      // associated them outside the form while parsing the long editor.
      document.querySelectorAll('[data-formset-prefix]').forEach(function (section) {
        section.querySelectorAll('input[name], select[name], textarea[name]').forEach(function (input) {
          if (input.disabled || input.closest('template') || payload.has(input.name)) return;
          if ((input.type === 'checkbox' || input.type === 'radio') && !input.checked) return;
          if (input.type === 'file') {
            Array.from(input.files).forEach(function (file) { payload.append(input.name, file); });
          } else if (input.tagName === 'SELECT' && input.multiple) {
            Array.from(input.selectedOptions).forEach(function (option) { payload.append(input.name, option.value); });
          } else {
            payload.append(input.name, input.value);
          }
        });
      });
      var response = await fetch(form.action, {
        method: 'POST', body: payload, credentials: 'same-origin',
        headers: {'Accept': 'application/json'}, signal: AbortSignal.timeout(30000)
      });
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error(response.status === 403 ? 'Sesija je istekla. Osvježite stranicu i pokušajte ponovo.' : 'Čuvanje nije potvrđeno. Provjerite prijavu i pokušajte ponovo.');
      }
      var result = await response.json();
      if (!response.ok) {
        status.className = 'settings-errors';
        status.textContent = 'Podešavanja nisu sačuvana. Ispravite sljedeća polja:';
        var errors = document.createElement('ul');
        (result.errors || ['Čuvanje nije uspjelo.']).forEach(function (error) {
          var item = document.createElement('li');
          item.textContent = error;
          errors.appendChild(item);
        });
        status.appendChild(errors);
        status.focus();
        status.scrollIntoView({block: 'start', behavior: 'smooth'});
        return;
      }
      dirty = false;
      status.textContent = result.message;
      window.location.assign(result.redirect);
    } catch (error) {
      status.className = 'settings-errors';
      status.textContent = error.name === 'TimeoutError' ? 'Server nije odgovorio na vrijeme. Provjerite vezu i pokušajte ponovo.' : error.message;
      status.focus();
      status.scrollIntoView({block: 'start', behavior: 'smooth'});
    } finally {
      saving = false;
      buttons.forEach(function (button) { button.disabled = false; });
    }
  });
  window.addEventListener('beforeunload', function (event) { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
