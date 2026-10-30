(() => {
    const form = document.getElementById('wmsPrintForm');
    if (!form) return;
    const selectAll = document.getElementById('wmsSelectAll');
    const print = document.getElementById('wmsPrintSelected');
    const count = document.getElementById('wmsSelectedCount');
    const orderCount = document.getElementById('wmsOrderCount');
    const validatedCount = document.getElementById('wmsValidatedCount');
    const emptyRow = document.getElementById('wmsEmptyRow');
    const showValidated = form.dataset.showValidated === '1';
    const checks = () => Array.from(form.querySelectorAll('.wms-order-check'));
    function update() {
        const all = checks();
        const selected = all.filter(check => check.checked).length;
        selectAll.checked = all.length > 0 && selected === all.length;
        selectAll.indeterminate = selected > 0 && selected < all.length;
        selectAll.disabled = all.length === 0;
        print.disabled = selected === 0;
        count.textContent = `Odabrano: ${selected}`;
        orderCount.textContent = all.length;
        emptyRow.hidden = all.length > 0;
    }
    selectAll.addEventListener('change', () => {
        checks().forEach(check => { check.checked = selectAll.checked; });
        update();
    });
    form.addEventListener('change', event => {
        if (event.target.matches('.wms-order-check')) update();
    });
    form.addEventListener('submit', event => {
        if (!checks().some(check => check.checked)) event.preventDefault();
    });
    let refreshing = false;
    async function refresh() {
        if (refreshing || document.hidden) return;
        refreshing = true;
        try {
            const response = await fetch(form.dataset.stateUrl, { credentials: 'same-origin', cache: 'no-store' });
            if (!response.ok || response.redirected) return;
            const state = await response.json();
            if (!Array.isArray(state.pending_ids) || !Number.isInteger(state.validated_count)) return;
            const previousValidatedCount = Number(validatedCount.textContent);
            validatedCount.textContent = state.validated_count;
            if (!showValidated) {
                const pendingIds = new Set(state.pending_ids.map(String));
                form.querySelectorAll('[data-wms-order-id]').forEach(row => {
                    if (!pendingIds.has(row.dataset.wmsOrderId)) row.remove();
                });
                update();
                const displayedIds = new Set(Array.from(form.querySelectorAll('[data-wms-order-id]'), row => row.dataset.wmsOrderId));
                if (!checks().some(check => check.checked) && state.pending_ids.some(id => !displayedIds.has(String(id)))) {
                    window.location.reload();
                }
            } else if (state.validated_count !== previousValidatedCount && !checks().some(check => check.checked)) {
                window.location.reload();
            }
        } catch (_) {
            // Keep the current selection when the connection is interrupted.
        } finally {
            refreshing = false;
        }
    }
    window.addEventListener('pageshow', () => { update(); refresh(); });
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    window.setInterval(refresh, 15000);
    update();
})();
