(() => {
 const quantity = document.getElementById('pickQuantity');
 const form = document.querySelector('[data-pick-draft]');
 const draftKey = form ? 'wms-pick-draft:' + form.dataset.pickDraft : null;
 const saveDraft = () => {
  if (!quantity || !draftKey) return;
  try { localStorage.setItem(draftKey, quantity.value); } catch (_) {}
 };
 if (quantity && draftKey) {
  try {
   const saved = localStorage.getItem(draftKey);
   if (saved !== null && saved !== '' && Number.isInteger(Number(saved)) && Number(saved) >= 0 && Number(saved) <= Number(quantity.max)) quantity.value = saved;
  } catch (_) {}
  quantity.addEventListener('input', saveDraft);
  window.addEventListener('pagehide', saveDraft);
 }

 document.querySelectorAll('[data-pick-step]').forEach(button => button.addEventListener('click', () => {
  quantity.value = Math.max(0, Math.min(Number(quantity.max), Number(quantity.value) + Number(button.dataset.pickStep)));
  saveDraft();
 }));
 const photo = document.getElementById('pickPhoto');
 if (!photo) return;
 const camera = document.getElementById('pickCamera');
 const preview = document.querySelector('.pick-photo-preview');
 let url;
 const update = () => {
  if (url) URL.revokeObjectURL(url);
  const file = photo.files[0];
  preview.hidden = !file;
  if (file) { url = URL.createObjectURL(file); preview.querySelector('img').src = url; }
 };
 photo.addEventListener('change', update);
 camera.addEventListener('change', () => { if (camera.files.length) { photo.files = camera.files; update(); } });
 document.querySelector('[data-camera]').addEventListener('click', () => camera.click());
 document.querySelector('[data-choose-photo]').addEventListener('click', () => photo.click());
 document.querySelector('[data-remove-photo]').addEventListener('click', () => { photo.value = ''; camera.value = ''; update(); });
})();
