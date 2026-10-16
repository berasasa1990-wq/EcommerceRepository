(function () {
    var button = document.getElementById('stockLoadMore');
    var list = document.querySelector('.stock-results');
    if (!button || !list) return;
    var pending = false;
    button.addEventListener('click', async function (event) {
        event.preventDefault();
        if (pending) return;
        pending = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = 'Učitavanje…';
        var error = document.getElementById('stockLoadError');
        error.textContent = '';
        try {
            var response = await fetch(button.href, {credentials: 'same-origin'});
            if (!response.ok || response.redirected) throw new Error('Osvježi stranicu i pokušaj ponovo.');
            var page = new DOMParser().parseFromString(await response.text(), 'text/html');
            var results = page.querySelector('.stock-results');
            if (!results) throw new Error('Rezultati nisu učitani. Pokušaj ponovo.');
            var existing = new Set(Array.from(list.querySelectorAll('.stock-result'), function (item) { return item.getAttribute('href'); }));
            results.querySelectorAll('.stock-result').forEach(function (item) {
                if (!existing.has(item.getAttribute('href'))) { existing.add(item.getAttribute('href')); list.appendChild(document.importNode(item, true)); }
            });
            var count = page.getElementById('stockShownCount');
            if (count) document.getElementById('stockShownCount').textContent = count.textContent;
            var next = page.getElementById('stockLoadMore');
            if (next) button.setAttribute('href', next.getAttribute('href'));
            else button.hidden = true;
        } catch (err) { error.textContent = err.message || 'Učitavanje nije uspjelo.'; }
        finally { pending = false; button.removeAttribute('aria-busy'); button.textContent = 'Prikaži više'; }
    });
})();
