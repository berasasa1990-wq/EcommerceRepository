(() => {
  const form = document.getElementById('footerNewsletterForm');
  const message = document.getElementById('footerNewsletterMsg');
  if (!form || !message) return;
  let pending = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending || !form.reportValidity()) return;
    pending = true;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    form.setAttribute('aria-busy', 'true');
    message.hidden = true;
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest'}
      });
      const data = await response.json().catch(() => null);
      const success = response.ok && data && data.ok;
      message.textContent = data && data.message || 'Prijava nije uspjela. Osvježite stranicu i pokušajte ponovo.';
      message.className = 'bera-newsletter__message ' + (success ? 'is-ok' : 'is-err');
      if (success) form.reset();
    } catch (_) {
      message.textContent = 'Greška mreže. Pokušajte ponovo.';
      message.className = 'bera-newsletter__message is-err';
    } finally {
      message.hidden = false;
      button.disabled = false;
      form.removeAttribute('aria-busy');
      pending = false;
    }
  });
})();
