/**
 * Prisutnost posjetioca: heartbeat + leave beacon.
 * Kad kupac zatvori tab (X), staff toast se skida odmah.
 */
(function () {
    const root = document.getElementById('visitorPresenceRoot');
    if (!root) return;

    const heartbeatUrl = root.dataset.heartbeatUrl || '/uzivo/prisutan/';
    const leaveUrl = root.dataset.leaveUrl || '/uzivo/odlazak/';
    const sessionKey = (root.dataset.sessionKey || '').trim();
    const heartbeatMs = 12000;
    let leftSent = false;
    let heartbeatTimer = null;

    function buildBody() {
        const params = new URLSearchParams();
        params.set('left', '1');
        if (sessionKey) params.set('session_key', sessionKey);
        return params.toString();
    }

    function sendHeartbeat() {
        if (leftSent) return;
        const params = new URLSearchParams();
        if (sessionKey) params.set('session_key', sessionKey);
        params.set('ping', '1');
        // Live „Sada:” — trenutna stranica za staff analitiku
        try {
            params.set('path', window.location.pathname || '/');
            const q = new URLSearchParams(window.location.search || '').get('q');
            if (q) params.set('q', q);
        } catch (err) {
            /* ignore */
        }
        fetch(heartbeatUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
            body: params.toString(),
            keepalive: true,
        }).catch(function () {
            /* tiho */
        });
    }

    function sendLeave() {
        if (leftSent) return;
        leftSent = true;
        if (heartbeatTimer) {
            window.clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }

        const body = buildBody();
        let beaconOk = false;

        try {
            if (navigator.sendBeacon) {
                // FormData + sendBeacon pouzdanije šalje cookie + body u modernim browserima
                const fd = new FormData();
                fd.append('left', '1');
                if (sessionKey) fd.append('session_key', sessionKey);
                beaconOk = navigator.sendBeacon(leaveUrl, fd);
            }
        } catch (err) {
            beaconOk = false;
        }

        // Backup: fetch keepalive (i ako beacon vrati false)
        try {
            fetch(leaveUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                credentials: 'same-origin',
                body: body,
                keepalive: true,
            }).catch(function () {
                /* tiho */
            });
        } catch (err) {
            /* tiho */
        }

        // Query-string fallback (neki browseri gutaju body na unload)
        if (!beaconOk) {
            try {
                const sep = leaveUrl.indexOf('?') >= 0 ? '&' : '?';
                const url =
                    leaveUrl +
                    sep +
                    'session_key=' +
                    encodeURIComponent(sessionKey || '') +
                    '&left=1';
                if (navigator.sendBeacon) {
                    navigator.sendBeacon(url);
                }
            } catch (err) {
                /* tiho */
            }
        }
    }

    function startHeartbeat() {
        if (heartbeatTimer) return;
        sendHeartbeat();
        heartbeatTimer = window.setInterval(sendHeartbeat, heartbeatMs);
    }

    // pagehide je najpouzdaniji signal zatvaranja taba (uključujući X)
    window.addEventListener('pagehide', sendLeave);
    window.addEventListener('beforeunload', sendLeave);
    window.addEventListener('unload', sendLeave);

    // Kad tab opet postane vidljiv (bfcache restore), nastavi ping
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            leftSent = false;
            startHeartbeat();
            sendHeartbeat();
        }
    });

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            leftSent = false;
            sendHeartbeat();
        }
    });

    startHeartbeat();
})();
