(() => {
    document.querySelectorAll('[data-payments-dialog]').forEach(button => {
        const dialog = document.getElementById(button.dataset.paymentsDialog);
        if (!dialog) return;
        button.addEventListener('click', () => {
            if (!dialog.open) dialog.showModal();
        });
        dialog.addEventListener('click', event => {
            if (event.target === dialog) {
                const rect = dialog.getBoundingClientRect();
                if (event.clientX < rect.left || event.clientX > rect.right ||
                    event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
            }
        });
    });
})();
