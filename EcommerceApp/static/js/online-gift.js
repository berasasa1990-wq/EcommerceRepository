(function () {
    'use strict';

    var overlay = document.getElementById('onlineGiftOverlay');
    if (!overlay) return;

    var intro = document.getElementById('ogIntro');
    var loading = document.getElementById('ogLoading');
    var result = document.getElementById('ogResult');
    var revealBtn = document.getElementById('ogRevealBtn');
    var declineBtn = document.getElementById('ogDeclineBtn');
    var resultEmoji = document.getElementById('ogResultEmoji');
    var resultTitle = document.getElementById('ogResultTitle');
    var resultMsg = document.getElementById('ogResultMsg');
    var productBox = document.getElementById('ogProduct');
    var productImg = document.getElementById('ogProductImg');
    var productName = document.getElementById('ogProductName');
    var productOld = document.getElementById('ogProductOld');
    var cartBtn = document.getElementById('ogCartBtn');
    var backBtn = document.getElementById('ogBackBtn');
    var titleEl = document.getElementById('ogTitle');
    var porukaEl = document.getElementById('ogPoruka');

    var claimUrl = overlay.getAttribute('data-claim-url') || '/online-nagrada/otkrij/';
    var dismissUrl = overlay.getAttribute('data-dismiss-url') || '/online-nagrada/zatvori/';
    var pollUrl = overlay.getAttribute('data-poll-url') || '/online-nagrada/status/';
    var cartUrl = overlay.getAttribute('data-cart-url') || '/korpa/';
    var registerUrl = overlay.getAttribute('data-register-url') || '/registracija/?next=/';
    var giftId = overlay.getAttribute('data-id') || '0';
    var delay = Math.max(0, parseInt(overlay.getAttribute('data-delay') || '0', 10) || 0);
    var showNow = overlay.getAttribute('data-show-now') === '1';
    var forceShow = overlay.getAttribute('data-force-show') === '1';
    var sideMode = overlay.getAttribute('data-side-mode') === '1';
    var requiresRegistration = overlay.getAttribute('data-requires-registration') === '1';

    // Uvijek bočni popup (ne fullscreen) — stranica ostaje skrolabilna
    sideMode = true;
    overlay.classList.add('og-side');
    overlay.setAttribute('data-side-mode', '1');
    var registerGate = document.getElementById('ogRegisterGate');
    var playGate = document.getElementById('ogPlayGate');
    var registerBtn = document.getElementById('ogRegisterBtn');
    var choiceHint = document.getElementById('ogChoiceHint');
    var closedKey = 'og_closed_' + giftId;
    var busy = false;
    var done = false;
    var isOpen = false;
    var pollTimer = null;
    var POLL_MS = 2800;

    // Nakon prijave (force_show) obriši sessionStorage da se popup opet otvori
    if (forceShow) {
        try { sessionStorage.removeItem(closedKey); } catch (e) {}
    }

    function setRequiresRegistration(on) {
        requiresRegistration = !!on;
        if (registerGate) {
            registerGate.hidden = !requiresRegistration;
            if (requiresRegistration) registerGate.removeAttribute('hidden');
            else registerGate.setAttribute('hidden', '');
        }
        if (playGate) {
            playGate.hidden = !!requiresRegistration;
            if (requiresRegistration) playGate.setAttribute('hidden', '');
            else playGate.removeAttribute('hidden');
        }
        if (choiceHint) {
            choiceHint.textContent = requiresRegistration
                ? 'Registrujte se da biste mogli igrati nagradnu igru.'
                : 'Ti biraš — možeš odigrati ili zatvoriti.';
        }
        if (registerBtn && registerUrl) {
            registerBtn.setAttribute('href', registerUrl);
        }
        overlay.setAttribute('data-requires-registration', requiresRegistration ? '1' : '0');
    }

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

    function clearClosed() {
        try { sessionStorage.removeItem(closedKey); } catch (e) {}
    }

    function open() {
        if (done || isOpen) return;
        if (!forceShow && isClosed()) return;
        if (forceShow) clearClosed();
        // Uvijek sa strane — ne blokira scroll (desktop + mobilni)
        sideMode = true;
        overlay.classList.add('og-side');
        overlay.setAttribute('data-side-mode', '1');
        overlay.hidden = false;
        overlay.removeAttribute('hidden');
        void overlay.offsetWidth;
        overlay.classList.add('is-open');
        document.documentElement.classList.add('og-side-open');
        document.body.classList.add('og-open', 'og-side-open');
        isOpen = true;
        showStep('intro');
    }

    function hideOverlay(markAsClosed) {
        if (markAsClosed !== false) markClosed();
        overlay.classList.remove('is-open');
        document.documentElement.classList.remove('og-side-open');
        document.body.classList.remove('og-open', 'og-side-open');
        isOpen = false;
        setTimeout(function () {
            overlay.hidden = true;
            overlay.setAttribute('hidden', '');
        }, 280);
    }

    function postDismiss(reason) {
        var t = csrf();
        if (!t) return;
        var body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', t);
        if (reason) body.set('reason', reason);
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
            keepalive: true,
        }).catch(function () {});
    }

    function goToRegister(e) {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }
        if (busy) return;
        // Sakrij i ne prikazuj gostu više; server pamti da poslije prijave odmah igra
        hideOverlay(true);
        postDismiss('register');
        var url = registerUrl || '/registracija/?next=/';
        window.setTimeout(function () {
            window.location.href = url;
        }, 80);
    }

    function close() {
        // X ili „Ne, hvala”
        if (busy) return;
        // Već odigrao — samo sakrij UI, ne šalji dismiss
        if (done) {
            hideOverlay();
            return;
        }
        hideOverlay();
        postDismiss('');
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
        if (gift.register_url) {
            registerUrl = gift.register_url;
            overlay.setAttribute('data-register-url', registerUrl);
        }
        if (typeof gift.requires_registration !== 'undefined') {
            setRequiresRegistration(!!gift.requires_registration);
        } else if (typeof gift.is_registered !== 'undefined') {
            setRequiresRegistration(!gift.is_registered);
        }
        if (gift.force_show) {
            forceShow = true;
            overlay.setAttribute('data-force-show', '1');
            clearClosed();
        }
        // Uvijek bočni — bez fullscreen-a
        sideMode = true;
        overlay.setAttribute('data-side-mode', '1');
        overlay.classList.add('og-side');
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

        // Reset akcija
        if (cartBtn) cartBtn.hidden = true;
        if (backBtn) backBtn.hidden = true;
        if (productBox) productBox.hidden = true;

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
                cartBtn.textContent = 'Otvori korpu';
            }
            bumpCart();
        } else if (won && (reward.type === 'percent' || reward.type === 'fixed_km' || reward.type === 'free_shipping')) {
            // Osvojio popust / dostavu — korpa ima smisla
            if (cartBtn) {
                cartBtn.hidden = false;
                cartBtn.href = cartUrl;
                cartBtn.textContent = reward.type === 'free_shipping'
                    ? 'Idi u korpu (gratis dostava)'
                    : 'Idi u korpu';
            }
        } else {
            // Nije osvojio — nazad na sajt, NE u korpu
            if (cartBtn) {
                cartBtn.hidden = true;
                cartBtn.removeAttribute('href');
            }
            if (backBtn) {
                backBtn.hidden = false;
                backBtn.textContent = 'Nazad na sajt';
            }
        }
        markClosed();
    }

    function reveal() {
        if (busy || done) return;

        if (requiresRegistration) {
            goToRegister();
            return;
        }

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
        if (done || isOpen) return;
        if (!forceShow && isClosed()) return;
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
            if (!forceShow && isClosed()) return;
            open();
        }).catch(function () {});
    }

    if (revealBtn) revealBtn.addEventListener('click', reveal);
    if (registerBtn) {
        registerBtn.addEventListener('click', goToRegister);
    }

    // Kad ne osvoji — „Nazad na sajt” zatvara popup (ostaje na istoj stranici)
    if (backBtn) {
        backBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (!busy) hideOverlay();
        });
    }

    // Zatvaranje: X ili „Ne, hvala” (data-og-close)
    overlay.querySelectorAll('[data-og-close]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (!busy) close();
        });
    });

    // Klik na backdrop / van kartice NE zatvara; ne blokiraj touch skrol stranice
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) return;
        e.stopPropagation();
    });
    var card = overlay.querySelector('.og-card');
    if (card) {
        card.addEventListener('click', function (e) {
            e.stopPropagation();
        });
        // Touch skrol unutar kartice — ne propagiraj preventDefault na body
        card.addEventListener('touchmove', function (e) {
            e.stopPropagation();
        }, { passive: true });
    }

    // Escape ne zatvara — samo X
    // (namjerno nema keydown listenera)

    // Odmah sa strane (delay 0) — ili nakon kratkog delay-a iz data-atributa
    if (showNow) {
        if (delay > 0) {
            setTimeout(open, delay * 1000);
        } else {
            open();
        }
    }

    // Poll za manuelni push (i ako auto još nije stigao)
    pollTimer = setInterval(poll, POLL_MS);
    // brzi prvi poll ako nije show_now
    if (!showNow) {
        setTimeout(poll, 400);
    }
})();
