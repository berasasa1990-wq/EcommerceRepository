(() => {
  const fitTables = () => {
    document.querySelectorAll('#result_list, .inline-group .tabular table, body.panel-blue-workspace main table').forEach((table) => {
      const headers = [...table.querySelectorAll('thead th')].map((header) => header.textContent.trim());
      if (!headers.length) return;
      table.querySelectorAll('tbody tr:not(.add-row)').forEach((row) => {
        [...row.children].forEach((cell, index) => {
          if (cell.colSpan > 1) return;
          const label = headers[index] || '';
          if (cell.dataset.label !== label) cell.dataset.label = label;
        });
      });
      table.classList.add('articles-fit-table');
    });
  };
  fitTables();
  const content = document.getElementById('content') || document.querySelector('body.panel-blue-workspace main');
  if (content) new MutationObserver(fitTables).observe(content, {childList: true, subtree: true});
})();
