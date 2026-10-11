(function () {
    'use strict';
    function init() {
        var form = document.querySelector('body.change-form #content-main form');
        if (!form || !form.querySelector('.form-row')) return;
        var bar = document.createElement('section');
        bar.className = 'retro-tools';
        bar.setAttribute('aria-label', 'Pronađi podešavanje');
        bar.innerHTML = '<p class="retro-tools-title">Pronađi podešavanje</p><div class="retro-tools-controls"><label>Pretraži naziv polja<input type="search" id="retroFieldSearch" placeholder="Npr. cijena, dostava, popust…" autocomplete="off"></label><label>Idi na grupu<select id="retroSectionJump"><option value="">Odaberi grupu podešavanja</option></select></label></div><p class="retro-search-message" role="status" aria-live="polite"></p><div class="retro-search-results"></div>';
        form.insertBefore(bar, form.firstChild);
        var search = bar.querySelector('input'), sections = bar.querySelector('select');
        var results = bar.querySelector('.retro-search-results'), message = bar.querySelector('.retro-search-message');
        var groups = [], target = null;
        function available(node) {
            for (var current = node; current && current !== form; current = current.parentElement) {
                if (current.hidden || current.classList.contains('akcija-section-hidden') || current.classList.contains('empty-form') || window.getComputedStyle(current).display === 'none') return false;
            }
            return true;
        }
        function title(group) {
            var heading = group.querySelector('h2, legend, summary');
            return heading ? heading.textContent.trim().replace(/\s+/g, ' ') : '';
        }
        function reveal(node, control) {
            for (var current = control || node; current && current !== form; current = current.parentElement) {
                if (current.tagName === 'DETAILS') current.open = true;
            }
            if (target) target.classList.remove('retro-field-target');
            target = node;
            node.classList.add('retro-field-target');
            node.scrollIntoView({block:'center',behavior:'auto'});
            if (control) control.focus({preventScroll:true});
        }
        function refreshSections() {
            groups = Array.from(form.querySelectorAll('fieldset.module, .inline-group')).filter(function (group) {
                return available(group) && title(group) && !group.closest('.empty-form') && !(group.tagName === 'FIELDSET' && group.closest('.inline-group'));
            });
            sections.replaceChildren(new Option('Odaberi grupu podešavanja',''));
            groups.forEach(function (group,index) { sections.add(new Option(title(group),String(index))); });
        }
        function normalize(text) {
            return text.toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'dj');
        }
        function find() {
            results.replaceChildren();
            var query = normalize(search.value.trim());
            if (!query) { message.textContent = ''; return; }
            var matches = [], seen = new Set();
            form.querySelectorAll('.form-row label[for]').forEach(function (label) {
                var control = document.getElementById(label.htmlFor);
                var row = label.closest('.form-row');
                if (!control || seen.has(control) || control.type === 'hidden' || !available(row)) return;
                if (!normalize(label.textContent).includes(query)) return;
                seen.add(control);
                matches.push({label:label,control:control,row:row});
            });
            matches.slice(0,20).forEach(function (match) {
                var button = document.createElement('button');
                button.type = 'button';
                button.textContent = match.label.textContent.trim().replace(/:$/, '');
                button.addEventListener('click',function () { reveal(match.row,match.control); });
                results.appendChild(button);
            });
            message.textContent = matches.length ? 'Pronađeno polja: ' + matches.length + (matches.length > 20 ? '. Prikazano prvih 20; suzi pretragu.' : '. Klikni na polje da ga otvoriš.') : 'Nema polja s tim nazivom. Pokušaj kraći naziv.';
        }
        search.addEventListener('input',find);
        search.addEventListener('keydown',function (event) {
            if (event.key === 'Enter') { event.preventDefault(); var first=results.querySelector('button'); if(first) first.click(); }
            if (event.key === 'Escape') { search.value=''; find(); }
        });
        sections.addEventListener('change',function () {
            if (sections.value === '') return;
            var group = groups[Number(sections.value)];
            if (group) reveal(group,group.querySelector('input:not([type=hidden]):not(:disabled),select:not(:disabled),textarea:not(:disabled)'));
        });
        form.addEventListener('change',function (event) {
            if (event.target.id === 'id_tip') window.setTimeout(function () { refreshSections(); find(); },0);
        });
        refreshSections();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init); else init();
})();
