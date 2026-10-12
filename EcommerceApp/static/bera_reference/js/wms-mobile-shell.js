(() => {
 const toggle = document.querySelector('.wms-mobile-menu');
 const sidebar = document.getElementById('wmsMobileSidebar');
 const backdrop = document.querySelector('.wms-mobile-backdrop');
 const content = document.querySelector('.wms-content');
 const header = document.querySelector('.wms-mobile-header');
 if (!toggle || !sidebar || !backdrop || !header) return;
 const root = document.documentElement;
 const mobile = window.matchMedia('(max-width: 800px)');
 let isOpen = false, savedScroll = 0;
 const syncHeader = () => {
  if (mobile.matches) root.style.setProperty('--wms-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
 };
 const setOpen = open => {
  const active = open && mobile.matches;
  if (active && !isOpen) {
   savedScroll = window.scrollY;
   document.body.style.setProperty('--wms-scroll-offset', `${-savedScroll}px`);
   sidebar.scrollTop = 0;
  }
  const restoreScroll = isOpen && !active;
  root.classList.toggle('wms-menu-open', active);
  document.body.classList.toggle('wms-menu-open', active);
  toggle.setAttribute('aria-expanded', String(active));
  toggle.setAttribute('aria-label', active ? 'Zatvori WMS meni' : 'Otvori WMS meni');
  backdrop.hidden = !active;
  sidebar.inert = mobile.matches && !active;
  if (content) content.inert = active;
  isOpen = active;
  if (restoreScroll) {
   document.body.style.removeProperty('--wms-scroll-offset');
   window.scrollTo({top: savedScroll, left: 0, behavior: 'instant'});
  }
  if (active) sidebar.querySelector('a')?.focus({preventScroll: true});
 };
 toggle.addEventListener('click', () => setOpen(!isOpen));
 backdrop.addEventListener('click', () => { setOpen(false); toggle.focus({preventScroll: true}); });
 document.addEventListener('keydown', event => {
  if (!isOpen) return;
  if (event.key === 'Escape') { setOpen(false); toggle.focus({preventScroll: true}); }
  if (event.key === 'Tab') {
   const links = [toggle, ...sidebar.querySelectorAll('a[href], button:not([disabled])')];
   const first = links[0], last = links[links.length - 1];
   if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus({preventScroll: true}); }
   else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus({preventScroll: true}); }
  }
 });
 mobile.addEventListener('change', () => { setOpen(false); syncHeader(); });
 window.addEventListener('resize', syncHeader, {passive: true});
 if (window.ResizeObserver) new ResizeObserver(syncHeader).observe(header);
 syncHeader();
 setOpen(false);
})();
