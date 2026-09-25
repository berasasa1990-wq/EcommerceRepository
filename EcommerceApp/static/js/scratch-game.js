(() => {
  const modal = document.getElementById('scratchGame');
  if (!modal || document.body.classList.contains('is-checkout-page')) return;

  const statusUrl = modal.dataset.statusUrl;
  const claimUrl = modal.dataset.claimUrl;
  const addToOrderUrl = modal.dataset.addToOrderUrl;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const delay = modal.dataset.immediate === '1' ? 0 : 25000;
  modal.querySelector('.scratch-game__close').onclick = () => {
    if (modal.dataset.afterCloseUrl) window.location.assign(modal.dataset.afterCloseUrl);
    else modal.hidden = true;
  };

  function drawFoil(context, width, height) {
    const foil = context.createLinearGradient(0, 0, width, height);
    foil.addColorStop(0, '#65696d'); foil.addColorStop(.18, '#eef0f1');
    foil.addColorStop(.36, '#8a8f93'); foil.addColorStop(.57, '#f4f5f5');
    foil.addColorStop(.76, '#70757a'); foil.addColorStop(1, '#ced1d2');
    context.globalCompositeOperation = 'source-over';
    context.fillStyle = foil;
    context.fillRect(0, 0, width, height);
    context.globalAlpha = .22;
    for (let i = 0; i < 260; i += 1) {
      context.fillStyle = i % 2 ? '#fff' : '#25282a';
      context.fillRect(Math.random() * width, Math.random() * height, 1 + Math.random() * 3, 1);
    }
    context.globalAlpha = 1;
    context.fillStyle = 'rgba(15,15,15,.7)';
    context.font = '900 22px sans-serif';
    context.textAlign = 'center';
    context.fillText('GREBI OVDJE', width / 2, height / 2 + 8);
  }

  setTimeout(async () => {
    try {
      const statusResponse = await fetch(statusUrl, { credentials: 'same-origin' });
      const status = await statusResponse.json();
      if (!status.eligible) return;

      // Nagrada se bira i trajno čuva na serveru prije prvog poteza.
      const claimResponse = await fetch(claimUrl, {
        method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrfToken },
      });
      const prize = await claimResponse.json();
      if (!prize.ok) return;

      const rewardLabel = document.getElementById('scratchGameReward');
      const productReveal = document.getElementById('scratchProductReveal');
      if (prize.product_offer) {
        rewardLabel.hidden = true;
        productReveal.hidden = false;
        document.getElementById('scratchRevealName').textContent = prize.product_name;
        document.getElementById('scratchRevealDiscount').textContent = `−${prize.product_discount}% POPUST`;
        document.getElementById('scratchRevealPrice').textContent = `${prize.product_price} KM`;
        document.getElementById('scratchRevealRegularPrice').textContent = `${prize.product_regular_price} KM`;
        const revealImage = document.getElementById('scratchRevealImage');
        revealImage.src = prize.product_image || '';
        revealImage.hidden = !prize.product_image;
      } else {
        rewardLabel.hidden = false;
        rewardLabel.textContent = prize.label;
      }
      modal.hidden = false;

      const canvas = document.getElementById('scratchGameCanvas');
      const context = canvas.getContext('2d');
      const bounds = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const cell = 18;
      const columns = Math.ceil(bounds.width / cell);
      const rows = Math.ceil(bounds.height / cell);
      const scratched = new Set();
      let finished = false;
      let previousPoint = null;

      canvas.width = bounds.width * ratio;
      canvas.height = bounds.height * ratio;
      context.scale(ratio, ratio);
      drawFoil(context, bounds.width, bounds.height);

      function showResult() {
        if (finished) return;
        finished = true;
        context.clearRect(0, 0, bounds.width, bounds.height);
        canvas.style.pointerEvents = 'none';
        if (prize.won) document.getElementById('scratchGameHint').hidden = true;
        const result = document.getElementById('scratchGameResult');
        result.hidden = prize.product_offer;
        if (!prize.product_offer) result.textContent = prize.won
          ? `Čestitamo! Osvojili ste ${prize.label}. ${prize.saved_to_account ? 'Nagrada je sačuvana na vašem nalogu' : 'Nagrada je sačuvana za ovu sesiju'} i može se automatski iskoristiti na sljedećoj narudžbi u naredna 24 sata.`
          : 'Više sreće sljedeći put!';
        if (prize.won && prize.product_offer) {
          modal.classList.add('scratch-game--product-result');
          const choice = document.getElementById('scratchProductChoice');
          choice.hidden = false;
          choice.querySelector('p').textContent = 'Želite li ovaj artikal dodati u kreiranu narudžbu po sniženoj cijeni?';
          document.getElementById('scratchSkipProduct').onclick = () => {
            window.location.assign(modal.dataset.afterCloseUrl || '/');
          };
          document.getElementById('scratchAddToOrder').onclick = async () => {
            const response = await fetch(addToOrderUrl, {
              method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrfToken },
            });
            const added = await response.json();
            if (added.ok) {
              window.location.assign(modal.dataset.afterCloseUrl || '/');
            } else result.textContent = added.detail || 'Artikal trenutno nije moguće dodati u narudžbu.';
          };
        }
      }

      function markScratched(x, y) {
        const radius = Math.ceil(24 / cell);
        for (let row = Math.max(0, Math.floor(y / cell) - radius); row <= Math.min(rows - 1, Math.floor(y / cell) + radius); row += 1) {
          for (let column = Math.max(0, Math.floor(x / cell) - radius); column <= Math.min(columns - 1, Math.floor(x / cell) + radius); column += 1) {
            if (Math.hypot((column + .5) * cell - x, (row + .5) * cell - y) <= 24) scratched.add(`${column}:${row}`);
          }
        }
        if (scratched.size / (columns * rows) >= .6) showResult();
      }

      function scratch(event) {
        if (finished) return;
        const rect = canvas.getBoundingClientRect();
        const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        context.globalCompositeOperation = 'destination-out';
        context.lineCap = 'round'; context.lineJoin = 'round'; context.lineWidth = 48;
        context.beginPath();
        context.moveTo(previousPoint?.x ?? point.x, previousPoint?.y ?? point.y);
        context.lineTo(point.x, point.y);
        context.stroke();
        previousPoint = point;
        markScratched(point.x, point.y);
      }

      canvas.addEventListener('pointerdown', event => { previousPoint = null; scratch(event); });
      canvas.addEventListener('pointermove', event => { if (event.buttons || event.pressure) scratch(event); });
      canvas.addEventListener('pointerup', () => { previousPoint = null; });
      canvas.addEventListener('pointerleave', () => { previousPoint = null; });
    } catch (_) {}
  }, delay);
})();
