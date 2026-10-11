(() => {
  const dialog = document.getElementById('moduleUnlockDialog');
  if (!dialog) return;
  const form = document.getElementById('moduleUnlockForm');
  const message = document.getElementById('moduleUnlockMessage');
  const submit = form.querySelector('[type=submit]');
  document.querySelectorAll('.admin-btn--locked,.wms-nav-locked').forEach(card => {
    const title = card.querySelector('h2,h3,.admin-btn-text strong,.admin-btn-text span')?.textContent.trim()
      || card.querySelectorAll(':scope > span')[1]?.textContent.trim()
      || card.textContent.replace('🔒', '').trim();
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'module-unlock-trigger';
    button.title = 'Zatraži otključavanje: ' + title;
    button.setAttribute('aria-label', button.title);
    button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0M12 14v3"/><path d="m18 3 3 3-3 3"/></svg>';
    button.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      form.elements.option.value = title;
      document.getElementById('moduleUnlockName').textContent = title;
      message.textContent = ''; submit.disabled = false; submit.hidden = false;
      dialog.showModal();
    });
    card.appendChild(button);
  });
  dialog.querySelectorAll('.module-unlock-close,.module-unlock-cancel').forEach(button => button.addEventListener('click', () => dialog.close()));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true; message.textContent = 'Slanje zahtjeva…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (response.redirected) throw new Error('Ponovo se prijavite da biste poslali zahtjev.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Slanje nije uspjelo.');
      message.textContent = data.message; submit.hidden = true;
    } catch (error) { message.textContent = error.message; }
    finally { submit.disabled = false; }
  });
})();
