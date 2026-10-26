(() => {
  const widget = document.querySelector('[data-chatbot]');
  if (!widget) return;
  const panel = widget.querySelector('.bera-chat-panel');
  const launcher = widget.querySelector('.bera-chat-launcher');
  const input = widget.querySelector('textarea');
  const form = widget.querySelector('form');
  const log = widget.querySelector('.bera-chat-messages');
  const status = widget.querySelector('.bera-chat-status');
  const suggestions = widget.querySelector('.bera-chat-suggestions');
  const initialSuggestions = [...suggestions.children].map(button => button.textContent);
  const welcome = log.firstElementChild.textContent;
  const reset = widget.querySelector('[data-chat-reset]');
  const guided = widget.dataset.guided === 'true';
  let guideStarted = false;
  let busy = false;
  let autoTimer;
  const seenKey = 'bera-chatbot-opened';
  const hasSeen = () => { try { return sessionStorage.getItem(seenKey) === 'yes'; } catch { return false; } };
  const markSeen = () => { try { sessionStorage.setItem(seenKey, 'yes'); } catch {} };
  const open = (manual = false) => {
    clearTimeout(autoTimer);
    panel.hidden = false;
    launcher.setAttribute('aria-expanded', 'true');
    launcher.setAttribute('aria-label', 'Zatvori razgovor s asistentom');
    markSeen();
    if (guided && !guideStarted && !busy) perform({action: 'guide_start'});
    if (manual) input.focus();
  };
  const close = () => {
    clearTimeout(autoTimer);
    panel.hidden = true;
    launcher.setAttribute('aria-expanded', 'false');
    launcher.setAttribute('aria-label', 'Otvori razgovor s asistentom');
    markSeen();
    if (widget.contains(document.activeElement)) launcher.focus();
  };
  launcher.addEventListener('click', () => panel.hidden ? open(true) : close());
  widget.querySelector('[data-chat-close]').addEventListener('click', close);
  widget.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) close(); });
  const message = (text, role) => {
    const item = document.createElement('div');
    item.className = `bera-chat-message bera-chat-message--${role}`;
    item.textContent = text;
    log.append(item);
    log.scrollTop = log.scrollHeight;
  };
  const showStatus = (text, error = false) => {
    status.textContent = text;
    status.hidden = !text;
    status.classList.toggle('is-error', error);
  };
  const request = async payload => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 35000);
    try {
      const response = await fetch(widget.dataset.endpoint, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
        body: JSON.stringify(payload), signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Asistent trenutno nije dostupan. Pokušajte kasnije.');
      return data;
    } finally { clearTimeout(timeout); }
  };
  const setBusy = value => {
    busy = value;
    form.querySelector('button').disabled = value;
    reset.disabled = value;
    suggestions.querySelectorAll('button').forEach(button => { button.disabled = value; });
  };
  const renderReply = data => {
    if (data.reply) message(data.reply, 'assistant');
    const replyStart = log.lastElementChild;
    if (data.products?.length) {
      const cards = document.createElement('div');
      cards.className = 'bera-chat-products';
      data.products.forEach(product => {
        if (!/^\/artikal\/\d+\/$/.test(product.url)) return;
        const link = document.createElement('a');
        link.href = product.url;
        if (product.image) {
          const image = document.createElement('img');
          image.src = product.image;
          image.alt = '';
          image.width = 56;
          image.height = 56;
          image.loading = 'lazy';
          link.append(image);
          link.classList.add('has-image');
        }
        const copy = document.createElement('div');
        const name = document.createElement('span');
        name.textContent = product.name;
        const price = document.createElement('strong');
        price.textContent = `${product.price} KM${product.available ? '' : ' · Nije na stanju'}`;
        copy.append(name, price);
        link.append(copy);
        cards.append(link);
      });
      log.append(cards);
    }
    if (data.guided) {
      guideStarted = true;
      suggestions.replaceChildren();
      (data.choices || []).forEach(option => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = option.label;
        button.dataset.guideAction = option.action;
        button.dataset.guideValue = option.value;
        suggestions.append(button);
      });
      suggestions.hidden = !suggestions.children.length;
      input.placeholder = data.step === 'budget' || data.step === 'results' ? 'Maksimalni budžet, npr. 500 KM…' : 'Odaberite ili upišite kategoriju…';
    }
    log.scrollTop = data.products?.length && replyStart ? Math.max(0, replyStart.offsetTop - log.offsetTop - 12) : log.scrollHeight;
  };
  async function perform(payload, userText = '') {
    if (busy) return;
    setBusy(true);
    if (userText) message(userText, 'user');
    input.value = '';
    if (!guided) suggestions.hidden = true;
    showStatus(guided ? 'Tražim ponudu…' : 'Asistent piše…');
    try {
      renderReply(await request(payload));
      showStatus('');
    } catch (error) {
      showStatus(error.name === 'AbortError' ? 'Odgovor traje duže nego obično. Pokušajte ponovo.' : error.message, true);
      if (payload.message) input.value = payload.message;
    } finally { setBusy(false); }
  }
  const send = () => {
    const text = input.value.trim();
    if (!text || busy) return;
    if (text.length > 1000) { showStatus('Poruka može sadržavati najviše 1000 znakova.', true); return; }
    perform({message: text}, text);
  };
  form.addEventListener('submit', event => { event.preventDefault(); send(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
  });
  suggestions.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || busy) return;
    if (button.dataset.guideAction) {
      perform({action: button.dataset.guideAction, value: button.dataset.guideValue}, button.textContent);
    } else {
      input.value = button.textContent;
      send();
    }
  });
  reset.addEventListener('click', async () => {
    if (busy) return;
    setBusy(true);
    try {
      await request({action: 'reset'});
      log.replaceChildren();
      message(welcome, 'assistant');
      input.value = '';
      suggestions.replaceChildren();
      if (guided) renderReply(await request({action: 'guide_start'}));
      else initialSuggestions.forEach(text => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = text;
        suggestions.append(button);
      });
      suggestions.hidden = !suggestions.children.length;
      showStatus('');
    } catch (error) { showStatus(error.message, true); }
    finally { setBusy(false); }
  });
  if (widget.dataset.autoOpen === 'true' && !hasSeen()) {
    autoTimer = setTimeout(() => {
      if (document.visibilityState === 'visible') open();
      else document.addEventListener('visibilitychange', () => { if (!hasSeen()) open(); }, {once: true});
    }, Math.max(0, Number(widget.dataset.delay) || 0) * 1000);
  }
})();
