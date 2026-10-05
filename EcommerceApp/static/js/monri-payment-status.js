/* Only a verified server callback can change the status read here. */
(() => {
    const waiting = document.getElementById('monriWaiting');
    if (!waiting) return;
    const message = document.getElementById('monriWaitingMessage');
    const heading = document.getElementById('monriWaitingHeading');
    const fallback = document.getElementById('monriWaitingFallback');
    const button = document.getElementById('monriCheckStatus');
    const deadline = Date.now() + 30000;
    let busy = false;
    let redirected = false;
    fallback.hidden = true;

    function showFallback() {
        heading.textContent = 'Potvrda plaćanja je u toku';
        message.hidden = true;
        fallback.hidden = false;
        button.hidden = false;
    }

    async function checkStatus() {
        if (busy || redirected) return;
        busy = true;
        button.disabled = true;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 4000);
        try {
            const response = await fetch(waiting.dataset.statusUrl, {
                credentials: 'same-origin', cache: 'no-store',
                headers: {Accept: 'application/json'}, signal: controller.signal,
            });
            if (response.ok) {
                const data = await response.json();
                if (data.status === 'paid' && data.success_url) {
                    const destination = new URL(data.success_url, window.location.origin);
                    if (destination.origin === window.location.origin) {
                        redirected = true;
                        window.location.replace(destination.href);
                    }
                }
            }
        } catch (_) {
            // A network error is never proof of payment or permission to retry payment.
        } finally {
            window.clearTimeout(timeout);
            busy = false;
            button.disabled = false;
        }
    }

    async function poll() {
        await checkStatus();
        if (redirected) return;
        if (Date.now() >= deadline) {
            showFallback();
            return;
        }
        window.setTimeout(poll, 1500);
    }
    button.addEventListener('click', checkStatus);
    poll();
})();
