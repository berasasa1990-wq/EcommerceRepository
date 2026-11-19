const pologForm = document.querySelector('#polog-form');
const pologDialog = document.querySelector('[data-polog-dialog]');
const dateDisplay = document.querySelector('[data-polog-date]');
const sarajevoDate = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Sarajevo',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
});
let submitting = false;

function updatePologDate() {
  const { day, month, year } = Object.fromEntries(
    sarajevoDate.formatToParts(new Date()).map(({ type, value }) => [type, value]),
  );
  const currentDate = [year, month, day].join('-');
  if (dateDisplay.dateTime === currentDate) return;
  dateDisplay.dateTime = currentDate;
  dateDisplay.textContent = [day, month, year, ''].join('.');
}

pologForm.addEventListener('submit', (event) => {
  if (submitting) {
    event.preventDefault();
    return;
  }
  if (event.submitter?.name === 'upisao' && pologDialog.open) {
    submitting = true;
    return;
  }
  event.preventDefault();
  if (!pologDialog.open) pologDialog.showModal();
});
pologDialog.querySelector('[data-polog-cancel]').addEventListener('click', () => pologDialog.close());
window.addEventListener('pageshow', () => {
  submitting = false;
  if (pologDialog.open) pologDialog.close();
  updatePologDate();
});
document.addEventListener('visibilitychange', updatePologDate);
window.setInterval(updatePologDate, 1000);

const historyDialog = document.querySelector('#polog-history');
const historyButton = document.querySelector('[data-polog-history-open]');
historyButton.addEventListener('click', (event) => {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  historyDialog.showModal();
});
historyDialog.addEventListener('close', () => {
  const url = new URL(window.location.href);
  if (url.searchParams.has('historija')) {
    url.searchParams.delete('historija');
    url.searchParams.delete('page');
    window.history.replaceState(window.history.state, '', url);
  }
  historyButton.focus();
});
if (historyDialog.hasAttribute('data-open-on-load')) historyDialog.showModal();

const bankDialog = document.querySelector('#polog-bank');
const bankButton = document.querySelector('[data-polog-bank-open]');
bankButton.addEventListener('click', () => bankDialog.showModal());
bankDialog.querySelector('[data-polog-bank-cancel]').addEventListener('click', () => bankDialog.close());
bankDialog.addEventListener('close', () => bankButton.focus());
if (bankDialog.hasAttribute('data-open-on-load')) bankDialog.showModal();
