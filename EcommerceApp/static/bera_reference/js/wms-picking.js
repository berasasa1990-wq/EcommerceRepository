(() => {
 const quantity = document.getElementById('pickQuantity');
 document.querySelectorAll('[data-pick-step]').forEach(button => button.addEventListener('click', () => {
  quantity.value = Math.max(0, Math.min(Number(quantity.max), Number(quantity.value) + Number(button.dataset.pickStep)));
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
