(function () {
    'use strict';

    var overlay = document.getElementById('onlineGiftOverlay');
    if (!overlay) return;

    var intro = document.getElementById('ogIntro');
    var loading = document.getElementById('ogLoading');
    var result = document.getElementById('ogResult');
    var revealBtn = document.getElementById('ogRevealBtn');
    var resultEmoji = document.getElementById('ogResultEmoji');
    var resultTitle = document.getElementById('ogResultTitle');
    var resultMsg = document.getElementById('ogResultMsg');
    var productBox = document.getElementById('ogProduct');
    var productImg = document.getElementById('ogProductImg');
    var productName = document.getElementById('ogProductName');
    var productOld = document.getElementById('ogProductOld');
    var cartBtn = document.getElementById('ogCartBtn');
    var titleEl = document.getElementById('ogTitle');
    var porukaEl = document.getElementById('ogPoruka');

    var claimUrl = overlay.getAttribute('data-claim-url') || '/online-nagrada/otkrij/';
    var dismissUrl = overlay.getAttribute('data-dismiss-url') || '/online-nagrada/zatvori/';
    var pollUrl = overlay.getAttribute('data-poll-url') || '/online-nagrada/status/';
    var cartUrl = overlay.getAttribute('data-cart-url') || '/korpa/';
    var giftId = overlay.getAttribute('data-id') || '0';
    var delay = Math.max(0, parseInt(overlay.getAttribute('data-delay') || '0', 10) || 0);
    var showNow = overlay.getAttribute('data-show-now') === '1';
    var closedKey = 'og_closed_' + giftId;
    var busy = false;
    var done = false;
    var isOpen = false;
    var pollTimer = null;
    var POLL_MS = 2800;

    function csrf() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function isClosed() {
        try {
            return sessionStorage.getItem(closedKey) === '1';
        } catch (e) {
            return false;
        }
    }

    function markClosed() {
        try { sessionStorage.setItem(closedKey, '1'); } catch (e) {}
    }

    function open() {
        if (done || isOpen) return;
        if (isClosed()) return;
        overlay.hidden = false;
        overlay.removeAttribute('hidden');
        void overlay.offsetWidth;
        overlay.classList.add('is-open');
        document.body.classList.add('og-open');
        isOpen = true;
        showStep('intro');
    }

    function close() {
        // Samo preko X dugmeta
        overlay.classList.remove('is-open');
        document.body.classList.remove('og-open');
        isOpen = false;
        setTimeout(function () {
            overlay.hidden = true;
            overlay.setAttribute('hidden', '');
        }, 280);
        markClosed();
        var t = csrf();
        if (!t) return;
        var body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', t);
        fetch(dismissUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': t,
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json',
            },
            credentials: 'same-origin',
            body: body.toString(),
        }).catch(function () {});
    }

    function bumpCart() {
        var btn = document.querySelector('[data-cart-count]');
        if (!btn) return;
        var cur = parseInt(btn.getAttribute('data-cart-count') || '0', 10) || 0;
        btn.setAttribute('data-cart-count', String(cur + 1));
        btn.classList.add('cart-btn--has-items');
    }

    function showStep(step) {
        if (intro) intro.hidden = step !== 'intro';
        if (loading) loading.hidden = step !== 'loading';
        if (result) result.hidden = step !== 'result';
    }

    function applyGiftMeta(gift) {
        if (!gift) return;
        if (gift.id) {
            giftId = String(gift.id);
            closedKey = 'og_closed_' + giftId;
            overlay.setAttribute('data-id', giftId);
        }
        if (titleEl && gift.naslov) titleEl.textContent = gift.naslov;
        if (porukaEl) {
            if (gift.poruka) {
                porukaEl.textContent = gift.poruka;
                porukaEl.hidden = false;
            } else {
                porukaEl.hidden = true;
            }
        }
        if (gift.claim_url) claimUrl = gift.claim_url;
        if (gift.dismiss_url) dismissUrl = gift.dismiss_url;
        if (gift.cart_url) cartUrl = gift.cart_url;
    }

    function showResult(data) {
        done = true;
        showStep('result');
        var won = !!data.won;
        var reward = data.reward || {};
        var product = reward.product || null;

        if (resultEmoji) resultEmoji.textContent = won ? '🎉' : '✨';
        if (resultTitle) resultTitle.textContent = data.title || (won ? 'Čestitamo!' : 'Sreću drugi put!');
        if (resultMsg) resultMsg.textContent = data.message || '';
        if (result) result.classList.toggle('is-win', won);

        if (won && product && productBox) {
            productBox.hidden = false;
            if (productImg && product.image) {
                productImg.src = product.image;
                productImg.alt = product.naziv || '';
            }
            if (productName) productName.textContent = product.naziv || 'Gratis artikal';
            if (productOld) {
                productOld.textContent = product.price
                    ? String(product.price).replace('.', ',') + ' KM'
                    : '';
            }
            if (cartBtn) {
                cartBtn.hidden = false;
                cartBtn.href = product.cart_url || cartUrl;
            }
            bumpCart();
        } else if (productBox) {
            productBox.hidden = true;
            if (cartBtn) {
                if (won && (reward.type === 'percent' || reward.type === 'fixed_km' || reward.type === 'free_shipping')) {
                    cartBtn.hidden = false;
                    cartBtn.href = cartUrl;
                    cartBtn.textContent = reward.type === 'free_shipping'
                        ? 'Idi u korpu (gratis dostava)'
                        : 'Idi u korpu';
                } else {
                    cartBtn.hidden = true;
                }
            }
        }
        markClosed();
    }

    function reveal() {
        if (busy || done) return;
        busy = true;
        showStep('loading');

        var t = csrf();
        if (!t) {
            busy = false;
            showStep('intro');
            alert('Osvježite stranicu.');
            return;
        }
        var body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', t);

        var started = Date.now();
        fetch(claimUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': t,
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json',
            },
            credentials: 'same-origin',
            body: body.toString(),
        }).then(function (res) {
            return res.text().then(function (raw) {
                var data;
                try { data = JSON.parse(raw); }
                catch (e) {
                    throw new Error(res.status === 403
                        ? 'Sesija istekla — osvježite stranicu.'
                        : 'Greška servera. Osvježite stranicu.');
                }
                if (!res.ok || !data.ok) {
                    throw new Error(data.message || 'Nagrada nije dostupna.');
                }
                var wait = Math.max(0, 1100 - (Date.now() - started));
                setTimeout(function () {
                    busy = false;
                    showResult(data);
                }, wait);
            });
        }).catch(function (err) {
            busy = false;
            showStep('intro');
            alert(err.message || 'Greška.');
        });
    }

    function poll() {
        if (done || isOpen || isClosed()) return;
        fetch(pollUrl, {
            method: 'GET',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json',
            },
            credentials: 'same-origin',
        }).then(function (res) {
            if (!res.ok) return null;
            return res.json();
        }).then(function (data) {
            if (!data || !data.active || !data.gift) return;
            if (data.csrf_token) {
                var meta = document.querySelector('meta[name="csrf-token"]');
                if (meta) meta.content = data.csrf_token;
            }
            applyGiftMeta(data.gift);
            if (isClosed()) return;
            open();
        }).catch(function () {});
    }

    if (revealBtn) revealBtn.addEventListener('click', reveal);

    // Zatvaranje SAMO na X (data-og-close na .og-x)
    overlay.querySelectorAll('.og-x[data-og-close]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (!busy) close();
        });
    });

    // Klik na backdrop / van kartice NE zatvara
    overlay.addEventListener('click', function (e) {
        e.stopPropagation();
    });
    var card = overlay.querySelector('.og-card');
    if (card) {
        card.addEventListener('click', function (e) {
            e.stopPropagation();
        });
    }

    // Escape ne zatvara — samo X
    // (namjerno nema keydown listenera)

    if (showNow) {
        setTimeout(open, delay * 1000);
    }

    // Poll za manuelni push (i ako auto još nije stigao)
    pollTimer = setInterval(poll, POLL_MS);
    // brzi prvi poll ako nije show_now
    if (!showNow) {
        setTimeout(poll, 800);
    }
})();
