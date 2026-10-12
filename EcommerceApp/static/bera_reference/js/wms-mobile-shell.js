(() => {
 const toggle = document.querySelector('.wms-mobile-menu');
 const sidebar = document.getElementById('wmsMobileSidebar');
 const backdrop = document.querySelector('.wms-mobile-backdrop');
 const content = document.querySelector('.wms-content');
 if (!toggle || !sidebar || !backdrop) return;
 const mobile = window.matchMedia('(max-width: 800px)');
 const setOpen = open => {
  const active = open && mobile.matches;
  document.body.classList.toggle('wms-menu-open', active);
  toggle.setAttribute('aria-expanded', String(active));
  toggle.setAttribute('aria-label', active ? 'Zatvori WMS meni' : 'Otvori WMS meni');
  backdrop.hidden = !active;
  sidebar.inert = mobile.matches && !active;
  if (content) content.inert = active;
  if (active) sidebar.querySelector('a')?.focus();
 };
 toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
 backdrop.addEventListener('click', () => { setOpen(false); toggle.focus(); });
 document.addEventListener('keydown', event => {
  if (toggle.getAttribute('aria-expanded') !== 'true') return;
  if (event.key === 'Escape') { setOpen(false); toggle.focus(); }
  if (event.key === 'Tab') {
   const links = [toggle, ...sidebar.querySelectorAll('a[href], button:not([disabled])')];
   const first = links[0], last = links[links.length - 1];
   if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
   else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
 });
 mobile.addEventListener('change', () => setOpen(false));
 setOpen(false);
})();
