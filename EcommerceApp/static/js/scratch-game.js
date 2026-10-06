(() => {
  const modal = document.getElementById('scratchGame');
  if (!modal || document.body.classList.contains('is-checkout-page')) return;

  const statusUrl = modal.dataset.statusUrl;
  const claimUrl = modal.dataset.claimUrl;
  const addToOrderUrl = modal.dataset.addToOrderUrl;
  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content;
  const requestHeaders = () => ({ 'X-CSRFToken': csrfToken(), 'X-Requested-With': 'XMLHttpRequest' });
  let dismissed = false;
  const closeGame = () => {
    dismissed = true;
    if (modal.dataset.afterCloseUrl) window.location.assign(modal.dataset.afterCloseUrl);
    else modal.hidden = true;
  };
  modal.querySelector('.scratch-game__close').onclick = closeGame;
  document.getElementById('scratchContinue').onclick = closeGame;

  function drawFoil(context, width, height) {
    const foil = context.createLinearGradient(0, 0, width, height);
    foil.addColorStop(0, '#ffffff'); foil.addColorStop(.18, '#faf9f5');
    foil.addColorStop(.36, '#f2efe7'); foil.addColorStop(.57, '#faf8f2');
    foil.addColorStop(.76, '#ffffff'); foil.addColorStop(1, '#f5f2eb');
    context.globalCompositeOperation = 'source-over';
    context.fillStyle = foil;
    context.fillRect(0, 0, width, height);
    context.globalAlpha = .08;
    for (let i = 0; i < 260; i += 1) {
      context.fillStyle = i % 2 ? '#fff' : '#25282a';
      context.fillRect(Math.random() * width, Math.random() * height, 1 + Math.random() * 3, 1);
    }
    context.globalAlpha = 1;
    context.fillStyle = '#8f7439';
    context.font = '900 22px sans-serif';
    context.textAlign = 'center';
    context.fillText('GREBI OVDJE', width / 2, height / 2 + 8);
  }

  async function openGame() {
    let ready = false;
    if (modal.dataset.immediate === '1') {
      modal.classList.add('scratch-game--loading');
      modal.hidden = false;
    }
    try {
      const statusResponse = await fetch(statusUrl, { credentials: 'same-origin' });
      const status = await statusResponse.json();
      if (!status.eligible) return;

      // Nagrada se bira i trajno čuva na serveru prije prvog poteza.
      const claimResponse = await fetch(claimUrl, {
        method: 'POST', credentials: 'same-origin', headers: requestHeaders(),
      });
      const prize = await claimResponse.json();
      if (!prize.ok || dismissed) return;
      const recordEvent = event => fetch(modal.dataset.eventUrl, {
        method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: requestHeaders(),
        body: new URLSearchParams({ claim_id: prize.claim_id, event }),
      }).catch(() => {});

      const rewardLabel = document.getElementById('scratchGameReward');
      const productReveal = document.getElementById('scratchProductReveal');
      if (prize.product_offer) {
        rewardLabel.hidden = true;
        productReveal.hidden = false;
        document.getElementById('scratchRevealName').textContent = prize.product_name;
        document.getElementById('scratchRevealDiscount').textContent = `−${Number(prize.product_discount)}%`;
        document.getElementById('scratchRevealPrice').textContent = `${prize.product_price} KM`;
        document.getElementById('scratchRevealRegularPrice').textContent = `${prize.product_regular_price} KM`;
        const revealImage = document.getElementById('scratchRevealImage');
        revealImage.src = prize.product_image || '';
        revealImage.hidden = !prize.product_image;
        const revealPack = document.getElementById('scratchRevealPack');
        revealPack.textContent = prize.product_pack ? `📦 ${prize.product_pack}` : '';
        revealPack.hidden = !prize.product_pack;
      } else {
        rewardLabel.hidden = false;
        const percentReward = Number(prize.reward_percent) > 0;
        rewardLabel.textContent = percentReward ? '−' + Number(prize.reward_percent) + '%' : prize.label;
        modal.classList.toggle('scratch-game--percent', percentReward);
      }
      modal.hidden = false;
      recordEvent('shown');

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
      let activePointerId = null;

      canvas.width = bounds.width * ratio;
      canvas.height = bounds.height * ratio;
      context.scale(ratio, ratio);
      drawFoil(context, bounds.width, bounds.height);
      ready = true;
      modal.classList.remove('scratch-game--loading');

      function showResult() {
        if (finished) return;
        finished = true;
        recordEvent('revealed');
        context.clearRect(0, 0, bounds.width, bounds.height);
        canvas.style.pointerEvents = 'none';
        const result = document.getElementById('scratchGameResult');
        modal.classList.add('scratch-game--revealed');
        document.getElementById('scratchGameTitle').textContent = prize.won ? 'ČESTITAMO!' : 'VIŠE SREĆE DRUGI PUT';
        const subtitle = document.getElementById('scratchGameSubtitle');
        subtitle.replaceChildren();
        if (prize.won) {
          subtitle.append('Osvojili ste');
          subtitle.appendChild(document.createElement('br'));
          if (prize.product_offer) {
            subtitle.append(prize.product_name);
            subtitle.appendChild(document.createElement('br'));
          }
          const highlight = document.createElement('strong');
          highlight.textContent = Number(prize.reward_percent) > 0 ? 'popust ' + Number(prize.reward_percent) + '%' : prize.label;
          subtitle.appendChild(highlight);
        }
        const offerProduct = Boolean(prize.won && prize.product_offer);
        const choice = document.getElementById('scratchProductChoice');
        choice.hidden = !offerProduct;
        if (offerProduct) modal.classList.add('scratch-game--product-result');
        document.getElementById('scratchContinue').hidden = offerProduct;
        result.hidden = offerProduct;
        if (!offerProduct) result.textContent = prize.won
          ? (prize.coupon_code
            ? `Vaš kod: ${prize.coupon_code}. ${prize.saved_to_account ? 'Sačuvan je na vašem nalogu.' : 'Kod šaljemo na email iz narudžbe.'} Unesite ga u korpi pri sljedećoj narudžbi. Kod nema roka isteka.`
            : 'Nagrada je sačuvana i nema roka isteka.')
          : 'Više sreće sljedeći put!';
        if (offerProduct) {
          choice.querySelector('p').textContent = 'Želite li ovaj artikal dodati u kreiranu narudžbu po sniženoj cijeni?';
          document.getElementById('scratchSkipProduct').onclick = () => {
            window.location.assign(modal.dataset.afterCloseUrl || '/');
          };
          const addButton = document.getElementById('scratchAddToOrder');
          addButton.onclick = async () => {
            if (addButton.disabled) return;
            addButton.disabled = true;
            const label = addButton.textContent;
            addButton.textContent = 'Dodajem…';
            try {
              const tokenResponse = await fetch(modal.dataset.csrfRefreshUrl, {
                credentials: 'same-origin', cache: 'no-store', headers: { 'Accept': 'application/json' },
              });
              if (!tokenResponse.ok) throw new Error('token');
              const tokenData = await tokenResponse.json();
              if (!tokenData.csrfToken) throw new Error('token');
              document.querySelector('meta[name="csrf-token"]').content = tokenData.csrfToken;
              const response = await fetch(addToOrderUrl, {
                method: 'POST', credentials: 'same-origin', headers: requestHeaders(),
              });
              let added;
              try {
                added = await response.json();
              } catch (_) {
                added = { detail: response.status === 403
                  ? 'Sigurnosna provjera nije uspjela. Osvježite stranicu i pokušajte ponovo.'
                  : `Server nije potvrdio dodavanje (greška ${response.status}). Pokušajte ponovo.` };
              }
              if (response.ok && added.ok) {
                window.location.assign(modal.dataset.afterCloseUrl || '/');
                return;
              }
              result.hidden = false;
              result.textContent = added.detail || added.error || 'Artikal trenutno nije moguće dodati u narudžbu.';
            } catch (_) {
              result.hidden = false;
              result.textContent = 'Dodavanje nije potvrđeno. Provjerite vezu i pokušajte ponovo.';
            }
            addButton.disabled = false;
            addButton.textContent = label;
          };
          choice.scrollIntoView({ block: 'nearest' });
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

      canvas.addEventListener('pointerdown', event => {
        activePointerId = event.pointerId;
        previousPoint = null;
        canvas.setPointerCapture?.(event.pointerId);
        event.preventDefault();
        scratch(event);
      });
      canvas.addEventListener('pointermove', event => {
        if (event.pointerId !== activePointerId) return;
        event.preventDefault();
        scratch(event);
      });
      function finishScratch(event) {
        if (event.pointerId !== activePointerId) return;
        canvas.releasePointerCapture?.(event.pointerId);
        activePointerId = null;
        previousPoint = null;
      }
      canvas.addEventListener('pointerup', finishScratch);
      canvas.addEventListener('pointercancel', finishScratch);
    } catch (_) {} finally {
      if (!ready) modal.hidden = true;
      modal.classList.remove('scratch-game--loading');
    }
  }

  if (modal.dataset.immediate === '1') openGame();
  else setTimeout(openGame, 25000);
})();
