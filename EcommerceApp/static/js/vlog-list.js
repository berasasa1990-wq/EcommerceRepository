(() => {
  const button = document.querySelector('[data-vlog-more]');
  const grid = document.querySelector('.vlog-list-grid');
  if (!button || !grid) return;
  button.addEventListener('click', () => {
    const remaining = [...grid.querySelectorAll('.home-vlog-card[hidden]')];
    remaining.slice(0, 8).forEach((card) => { card.hidden = false; });
    button.hidden = remaining.length <= 8;
    remaining[0]?.focus({ preventScroll: true });
  });
})();
