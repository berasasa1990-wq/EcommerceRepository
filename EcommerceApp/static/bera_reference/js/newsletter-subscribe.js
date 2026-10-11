(() => {
 const form = document.getElementById('footerNewsletterForm');
 const message = document.getElementById('footerNewsletterMsg');
 if (!form || !message) return;
 form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  if (button.disabled || !form.reportValidity()) return;
  button.disabled = true;
  try {
   const response = await fetch(form.action, {method:'POST', body:new FormData(form), credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'}});
   const data = await response.json();
   message.textContent = data.message;
   if(response.ok && data.ok) form.reset();
  } catch (_) {message.textContent = 'Prijava nije uspjela. Pokušajte ponovo.';}
  finally {message.hidden = false;button.disabled = false;}
 });
})();
