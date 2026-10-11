document.addEventListener('DOMContentLoaded', function () {
    const select = document.querySelector('[data-complaint-order]');
    if (!select) return;
    select.addEventListener('change', function () {
        if (select.value) select.form.requestSubmit();
    });
});
