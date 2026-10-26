// Login in another tab rotates the cookie; refresh this form's token before POST.
document.querySelectorAll('[data-auth-csrf-url]').forEach(form => {
  let sending = false;
  let refreshed = false;
  const feedback = document.createElement('p');
  feedback.className = 'account-feedback';
  feedback.setAttribute('role', 'alert');
  feedback.hidden = true;
  form.prepend(feedback);

  form.addEventListener('submit', async event => {
    if (refreshed) return;
    event.preventDefault();
    if (sending) return;
    sending = true;
    feedback.hidden = true;
    const button = event.submitter;
    if (button) button.disabled = true;
    try {
      const response = await fetch(form.dataset.authCsrfUrl, {
        credentials: 'same-origin', cache: 'no-store', mode: 'same-origin',
      });
      if (!response.ok) throw new Error('Token refresh failed');
      const data = await response.json();
      if (typeof data.csrfToken !== 'string' || !/^[a-zA-Z0-9]{64}$/.test(data.csrfToken)) {
        throw new Error('Invalid token');
      }
      form.querySelector('[name="csrfmiddlewaretoken"]').value = data.csrfToken;
      if (button) button.disabled = false;
      refreshed = true;
      form.requestSubmit(button || undefined);
    } catch (error) {
      feedback.textContent = 'Nije moguće poslati prijavu. Provjerite vezu i pokušajte ponovo.';
      feedback.hidden = false;
    } finally {
      sending = false;
      refreshed = false;
      if (button) button.disabled = false;
    }
  });
});
