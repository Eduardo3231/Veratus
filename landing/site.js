'use strict';

// The page arrives complete from scripts/build_landing.py. This file adds the
// visit reference to every WhatsApp link, the watch view (the stage turns the
// watch as the visitor scrolls) and the Meta Pixel events.

const phone = '5511958323612';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const allowedCampaignKeys = new Set(['utm_source', 'utm_medium', 'utm_campaign', 'utm_content']);

function campaignEntries() {
  return [...new URLSearchParams(window.location.search).entries()]
    .map(([key, value]) => [key.toLowerCase(), value.trim().slice(0, 80)])
    .filter(([key, value]) => allowedCampaignKeys.has(key) && value);
}

function campaignReference() {
  const source = campaignEntries().map(([key, value]) => `${key}=${value}`).join('&') || 'site-direto';
  let hash = 2166136261;
  for (const character of source) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `VT-${(hash >>> 0).toString(36).toUpperCase()}`;
}

function createWhatsAppLink(productName = '', productId = '', order = false) {
  const context = campaignEntries().map(([key, value]) => `${key}=${value}`).join(' | ');
  const message = [
    productName
      ? `Olá! Vim pelo site da Veratus e tenho interesse em ${productName}.`
      : 'Olá! Vim pelo site da Veratus e quero conhecer as coleções.',
    order ? 'Quero fazer o pedido e confirmar a disponibilidade.' : 'Gostaria de confirmar disponibilidade e valor.',
    `Referência da visita: ${campaignReference()}${productId ? ` | produto=${productId}` : ''}`,
    context ? `Origem da visita: ${context}` : '',
  ].filter(Boolean).join('\n');
  return `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
}

function refreshPurchaseLink(link) {
  link.href = createWhatsAppLink(link.dataset.productName, link.dataset.productId, link.dataset.order === '1');
}

// Every WhatsApp link carries the visit reference, the watch view CTA included.
document.querySelectorAll('.purchase-link').forEach(refreshPurchaseLink);

const products = [...document.querySelectorAll('[data-item]')].map((item) => ({
  id: item.dataset.productId,
  name: item.dataset.name,
  value: Number(item.dataset.value),
}));

// Meta Pixel: the WhatsApp conversation is the site's conversion (tracking ADR,
// option 1). Only product id, name and the public price travel; never the message.
function trackPixel(event, data) {
  if (typeof window.fbq === 'function') window.fbq('track', event, data);
}

function pixelProduct(productId) {
  const product = products.find((item) => item.id === productId);
  if (!product) return {};
  const data = { content_ids: [product.id], content_name: product.name, content_type: 'product' };
  if (Number.isFinite(product.value) && product.value > 0) Object.assign(data, { value: product.value, currency: 'BRL' });
  return data;
}

document.addEventListener('click', (event) => {
  const link = event.target.closest?.('a[href^="https://wa.me/"]');
  if (link) trackPixel('Contact', pixelProduct(link.dataset.productId));
});

function productImage(product) {
  return product.card.querySelector('img').getAttribute('src');
}

// Header state and the phone's bottom bar, which only exists after the hero.
const header = document.querySelector('.topo');
const mobileBar = document.querySelector('[data-mobile-bar]');
const hero = document.querySelector('#hero');
function updateScrollEffects() {
  header.classList.toggle('is-rolado', window.scrollY > 30);
  const visible = window.scrollY > hero.offsetHeight - 120;
  mobileBar.classList.toggle('is-visivel', visible);
  mobileBar.inert = !visible;
}
window.addEventListener('scroll', updateScrollEffects, { passive: true });
updateScrollEffects();
document.querySelector('[data-year]').textContent = String(new Date().getFullYear());

// Phone strips: with mandatory snap, Tab moved focus to the next card while the
// strip snapped back to the previous one, leaving the focused link half off-screen.
document.querySelectorAll('.relogios__lista').forEach((list) => {
  list.addEventListener('focusin', (event) => {
    const card = event.target.closest('.relogio');
    if (!card || list.scrollWidth <= list.clientWidth) return;
    const inset = parseFloat(getComputedStyle(list).scrollPaddingLeft) || 0;
    const left = list.scrollLeft + card.getBoundingClientRect().left - list.getBoundingClientRect().left - inset;
    list.scrollTo({ left, behavior: reducedMotion ? 'auto' : 'smooth' });
  });
});

// Watch view. Scrolling the view turns the watch; the pointer tilts it.
const watches = [...document.querySelectorAll('.relogio')].map((card) => ({
  card,
  id: card.dataset.productId,
  name: card.dataset.name,
  style: card.dataset.style,
  description: card.dataset.description,
  price: card.dataset.price,
  alt: card.dataset.alt,
  fx: card.dataset.fx,
  fy: card.dataset.fy,
}));
const view = document.querySelector('#vista');
const stage = view.querySelector('[data-stage]');
const stageImage = view.querySelector('[data-view-image]');
const ambience = stage.querySelector('.palco__ambiente');
const others = view.querySelector('[data-view-others]');
let pushedHistory = false;
let pointer = { x: 0, y: 0 };
let frame = 0;
let opener = null;

// Sandboxed viewers (local file, previews) may refuse history changes.
function changeHistory(method, state, url) {
  try {
    window.history[method](state, '', url);
    return true;
  } catch (_) {
    return false;
  }
}

function urlFor(id) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('relogio', id);
  else url.searchParams.delete('relogio');
  url.hash = '';
  return url;
}

function thumbnail(product) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.switch = product.id;
  const frameElement = document.createElement('span');
  frameElement.className = 'mini';
  frameElement.style.setProperty('--fx', product.fx);
  frameElement.style.setProperty('--fy', product.fy);
  const image = document.createElement('img');
  image.src = productImage(product);
  image.alt = '';
  image.loading = 'lazy';
  frameElement.append(image);
  const name = document.createElement('span');
  name.textContent = product.name;
  button.append(frameElement, name);
  const item = document.createElement('li');
  item.append(button);
  return item;
}

function fillView(product, track) {
  const image = productImage(product);
  stageImage.src = image;
  stageImage.alt = product.alt;
  ambience.style.backgroundImage = `url("${image}")`;
  view.querySelector('[data-view-name]').textContent = product.name;
  view.querySelector('[data-view-style]').textContent = product.style;
  view.querySelector('[data-view-description]').textContent = product.description;
  view.querySelectorAll('[data-view-price]').forEach((price) => { price.textContent = product.price; });
  view.querySelector('[data-view-position]').textContent = `${watches.indexOf(product) + 1} de ${watches.length}`;
  view.querySelectorAll('[data-view-order]').forEach((link, index) => {
    Object.assign(link.dataset, { productId: product.id, productName: product.name, order: '1' });
    refreshPurchaseLink(link);
    link.textContent = index === 0 ? `Pedir o ${product.name} pelo WhatsApp` : 'Pedir pelo WhatsApp';
    link.setAttribute('aria-label', `Pedir o ${product.name} pelo WhatsApp`);
  });
  others.replaceChildren(...watches.filter((item) => item !== product).map(thumbnail));
  if (track) trackPixel('ViewContent', pixelProduct(product.id));
}

function applyTurn() {
  frame = 0;
  if (reducedMotion) return;
  const range = view.scrollHeight - view.clientHeight;
  const progress = range > 0 ? Math.min(view.scrollTop / range, 1) : 0.5;
  const turn = -26 + 52 * progress + pointer.x * 12;
  stage.style.setProperty('--giro', `${turn.toFixed(2)}deg`);
  stage.style.setProperty('--incl', `${(pointer.y * -6).toFixed(2)}deg`);
  stage.style.setProperty('--reflexo', `${(progress * 100).toFixed(1)}%`);
  stage.style.setProperty('--mostrador', `${(progress * 150).toFixed(1)}deg`);
}

function requestTurn() {
  if (!frame) frame = window.requestAnimationFrame(applyTurn);
}

function enterStage() {
  stage.classList.remove('is-girado', 'is-entrando');
  if (reducedMotion) return;
  void stage.offsetWidth; // restarts the entrance animation
  stage.classList.add('is-entrando');
}

function openView(id, { push = true, track = true } = {}) {
  const product = watches.find((item) => item.id === id);
  if (!product) return;
  fillView(product, track);
  if (!view.open) {
    opener = document.activeElement;
    view.showModal();
    document.body.classList.add('vista-aberta');
  }
  view.scrollTop = 0;
  enterStage();
  if (push) pushedHistory = changeHistory('pushState', { relogio: id }, urlFor(id));
  applyTurn();
}

function closeView() {
  if (!view.open) return;
  if (pushedHistory && window.history.state?.relogio) {
    window.history.back(); // popstate closes the view
    return;
  }
  view.close();
}

document.addEventListener('click', (event) => {
  const trigger = event.target.closest('[data-open]');
  if (!trigger || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
  const card = trigger.closest('.relogio');
  if (!card) return;
  event.preventDefault();
  openView(card.dataset.productId);
});

others.addEventListener('click', (event) => {
  const button = event.target.closest('[data-switch]');
  if (!button) return;
  openView(button.dataset.switch, { push: false });
  changeHistory('replaceState', { relogio: button.dataset.switch }, urlFor(button.dataset.switch));
  view.querySelector('[data-view-close]').focus();
});

view.querySelector('[data-view-close]').addEventListener('click', closeView);
view.addEventListener('cancel', (event) => {
  event.preventDefault();
  closeView();
});
view.addEventListener('close', () => {
  document.body.classList.remove('vista-aberta');
  if (new URL(window.location.href).searchParams.has('relogio')) changeHistory('replaceState', null, urlFor(null));
  pushedHistory = false;
  opener?.focus?.();
});
window.addEventListener('popstate', () => {
  const id = new URL(window.location.href).searchParams.get('relogio');
  if (id) {
    // Back or Forward onto a view: it was counted when first opened, and an
    // entry this page pushed is left with Back, not rewritten.
    openView(id, { push: false, track: false });
    pushedHistory = Boolean(window.history.state?.relogio);
  } else if (view.open) {
    pushedHistory = false;
    view.close();
  }
});

view.addEventListener('scroll', () => {
  if (view.scrollTop > 40) stage.classList.add('is-girado');
  requestTurn();
}, { passive: true });

if (!reducedMotion && window.matchMedia('(pointer: fine)').matches) {
  stage.addEventListener('pointermove', (event) => {
    const box = stage.getBoundingClientRect();
    pointer = { x: (event.clientX - box.left) / box.width - 0.5, y: (event.clientY - box.top) / box.height - 0.5 };
    requestTurn();
  });
  stage.addEventListener('pointerleave', () => {
    pointer = { x: 0, y: 0 };
    requestTurn();
  });
}

// Direct link to a model (ad, Direct, bio): ?relogio=ocean-blue
const initial = new URL(window.location.href).searchParams.get('relogio');
if (initial) openView(initial, { push: false });
