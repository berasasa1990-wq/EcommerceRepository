/**
 * Prisutnost posjetioca: heartbeat + leave beacon.
 * Leave samo na pagehide (ne 3× beforeunload/unload) + leave_at da server
 * ignoriše leave kad je navigacija unutar sajta (nova stranica već trackana).
 */
(function () {
    const root = document.getElementById('visitorPresenceRoot');
    if (!root) return;

    const heartbeatUrl = root.dataset.heartbeatUrl || '/uzivo/prisutan/';
    const leaveUrl = root.dataset.leaveUrl || '/uzivo/odlazak/';
    const sessionKey = (root.dataset.sessionKey || '').trim();
    const heartbeatMs = 4000;
    const pageLoadedAt = Date.now();
    let leftSent = false;
    let heartbeatTimer = null;

    function sendHeartbeat() {
        if (leftSent) return;
        const params = new URLSearchParams();
        if (sessionKey) params.set('session_key', sessionKey);
        params.set('ping', '1');
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

        // leave_at = trenutak napuštanja ove stranice (ms)
        const leaveAt = String(Date.now());
        const body = new URLSearchParams();
        body.set('left', '1');
        body.set('leave_at', leaveAt);
        if (sessionKey) body.set('session_key', sessionKey);

        let beaconOk = false;
        try {
            if (navigator.sendBeacon) {
                const fd = new FormData();
                fd.append('left', '1');
                fd.append('leave_at', leaveAt);
                if (sessionKey) fd.append('session_key', sessionKey);
                beaconOk = navigator.sendBeacon(leaveUrl, fd);
            }
        } catch (err) {
            beaconOk = false;
        }

        try {
            fetch(leaveUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                credentials: 'same-origin',
                body: body.toString(),
                keepalive: true,
            }).catch(function () {
                /* tiho */
            });
        } catch (err) {
            /* tiho */
        }

        if (!beaconOk) {
            try {
                const sep = leaveUrl.indexOf('?') >= 0 ? '&' : '?';
                const url =
                    leaveUrl +
                    sep +
                    'session_key=' +
                    encodeURIComponent(sessionKey || '') +
                    '&left=1&leave_at=' +
                    encodeURIComponent(leaveAt);
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

    // Samo pagehide — beforeunload+unload su uzrokovali trostruki leave i race s navigacijom
    window.addEventListener('pagehide', function () {
        sendLeave();
    });

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

    // Prvi ping odmah (staff vidi „Sada:” bez čekanja)
    startHeartbeat();
    // Drugi ping ~0.6 s (session cookie + early track)
    window.setTimeout(function () {
        if (!leftSent) sendHeartbeat();
    }, 600);
})();
