const buyersButton = document.querySelector('[data-vo-buyers-url]');
const buyersDialog = document.querySelector('#vo-web-buyers');
const buyersContent = buyersDialog.querySelector('[data-vo-buyers-content]');
const buyersStatus = buyersDialog.querySelector('[data-vo-buyers-status]');
const messageDialog = document.querySelector('#vo-viber-message');
const messageForm = messageDialog.querySelector('[data-vo-viber-form]');
const copyButton = messageForm.querySelector('[type="submit"]');
const assignedCoupon = messageForm.querySelector('[data-vo-assigned-coupon]');
const couponButton = messageForm.querySelector('[data-vo-coupon-open]');
const couponDialog = document.querySelector('#vo-buyer-coupon');
const couponForm = couponDialog.querySelector('[data-vo-coupon-form]');
const couponStatus = couponDialog.querySelector('[data-vo-coupon-status]');
const couponSubmit = couponForm.querySelector('[type="submit"]');
const couponClose = couponForm.querySelector('[data-vo-close]');
const draft = messageDialog.querySelector('#vo-viber-draft');
const draftTemplate = draft.value;
const viberLink = messageDialog.querySelector('[data-vo-open-viber]');
const messageStatus = messageDialog.querySelector('[data-vo-viber-status]');
let buyersRequest;
let viberUrl = '';
let couponUrl = '';
let assigningCoupon = false;

buyersButton.addEventListener('click', async () => {
  buyersDialog.showModal();
  buyersContent.replaceChildren();
  buyersStatus.textContent = 'Učitavanje kupaca…';
  buyersRequest?.abort();
  const request = new AbortController();
  buyersRequest = request;
  try {
    const response = await fetch(buyersButton.dataset.voBuyersUrl, { signal: request.signal });
    if (!response.ok || response.redirected) throw new Error();
    const html = await response.text();
    if (!buyersDialog.open || request.signal.aborted) return;
    buyersContent.innerHTML = html;
    buyersStatus.textContent = '';
  } catch (error) {
    if (error.name !== 'AbortError') buyersStatus.textContent = 'Kupce nije bilo moguće učitati. Pokušaj ponovo.';
  }
});

buyersDialog.addEventListener('close', () => {
  buyersRequest?.abort();
  buyersContent.replaceChildren();
});

buyersContent.addEventListener('click', (event) => {
  const button = event.target.closest('[data-buyer-viber]');
  if (!button) return;
  viberUrl = button.dataset.buyerViber;
  couponUrl = button.dataset.buyerCouponUrl;
  messageDialog.querySelector('[data-vo-viber-recipient]').textContent = button.dataset.buyerName;
  messageForm.reset();
  assignedCoupon.textContent = '';
  copyButton.disabled = true;
  viberLink.hidden = true;
  viberLink.removeAttribute('href');
  messageStatus.textContent = '';
  messageDialog.showModal();
  couponButton.focus();
});

messageDialog.querySelector('[data-vo-close]').addEventListener('click', () => messageDialog.close());

couponButton.addEventListener('click', () => {
  couponForm.reset();
  couponStatus.textContent = '';
  couponDialog.showModal();
  couponForm.elements.kod.focus();
});
couponClose.addEventListener('click', () => couponDialog.close());
couponDialog.addEventListener('cancel', (event) => {
  if (assigningCoupon) event.preventDefault();
});

couponForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (assigningCoupon) return;
  assigningCoupon = true;
  couponSubmit.disabled = true;
  couponClose.disabled = true;
  couponStatus.textContent = 'Dodjeljivanje koda…';
  try {
    const response = await fetch(couponUrl, { method: 'POST', body: new FormData(couponForm) });
    if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error();
    const result = await response.json();
    if (!response.ok) {
      couponStatus.textContent = result.error || 'Dodjela koda nije uspjela.';
      return;
    }
    const instructions = result.claim_url
      ? 'Prije unosa koda otvori lični link iz ove poruke: ' + result.claim_url
      : 'Za korištenje koda prijavi se na svoj račun na webshopu.';
    const expiryNote = 'Kod vrijedi 7 dana od dodjele.';
    const cartNote = 'U korpi ga možeš odabrati preko dugmeta „Primijeni kupon ili karticu“.';
    draft.value = draftTemplate.replace(
      '[KOD]', () => result.code + '\n' + expiryNote + '\n\n' + instructions + '\n' + cartNote,
    );
    assignedCoupon.textContent = result.code + ' · ' + result.percent + '%';
    copyButton.disabled = false;
    viberLink.hidden = true;
    messageStatus.textContent = 'Kod je dodijeljen kupcu.';
    couponDialog.close();
  } catch {
    couponStatus.textContent = 'Dodjela koda nije uspjela. Pokušaj ponovo.';
  } finally {
    assigningCoupon = false;
    couponSubmit.disabled = false;
    couponClose.disabled = false;
  }
});

messageForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (copyButton.disabled) return;
  const target = viberUrl;
  const text = draft.value;
  viberLink.href = target;
  viberLink.hidden = false;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    if (!messageDialog.open || target !== viberUrl || text !== draft.value) return;
    draft.focus();
    draft.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch {}
    if (!copied) {
      messageStatus.textContent = 'Kopiraj označenu poruku, zatim klikni Otvori Viber.';
      return;
    }
  }
  if (!messageDialog.open || target !== viberUrl || text !== draft.value) return;
  messageStatus.textContent = 'Poruka je kopirana. Zalijepi je u razgovor pa klikni Pošalji.';
  window.location.assign(target);
});
