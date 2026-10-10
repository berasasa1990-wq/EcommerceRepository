document.addEventListener('DOMContentLoaded', () => {
  const section = document.querySelector('.product360-admin-section');
  const inline = document.getElementById('dodatne_slike-group');
  if (section && inline) inline.after(section);
  const deletion = document.getElementById('id_delete_360');
  if (deletion && section) {
    const action = document.createElement('button'); action.type = 'button'; action.className = 'spin-delete-button'; action.textContent = 'Obriši 360° fotografije';
    deletion.closest('.form-row').append(action);
    action.addEventListener('click', () => {
      deletion.checked = !deletion.checked;
      action.textContent = deletion.checked ? 'Brisanje označeno — sačuvajte artikal' : 'Obriši 360° fotografije';
    });
  }
  document.querySelectorAll('[data-spin-preview]').forEach(button => button.addEventListener('click', () => {
    const frames = JSON.parse(button.dataset.spinPreview); if (frames.length < 2) return;
    const dialog = document.createElement('dialog'); dialog.className = 'spin-admin-dialog';
    dialog.innerHTML = '<button type="button" class="spin-close" aria-label="Zatvori">×</button><div class="spin-admin-view"></div>';
    document.body.append(dialog); dialog.showModal();
    const viewer = window.BeraProduct360(dialog.querySelector('.spin-admin-view'), JSON.parse(button.dataset.spinPreview));
    dialog.querySelector('.spin-close').onclick = () => dialog.close();
    dialog.addEventListener('close', () => { viewer.destroy(); dialog.remove(); }, {once: true});
  }));
});
