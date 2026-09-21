const phone = '5511958323612';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const catalogSection = document.querySelector('#catalogo');
const catalogGrid = document.querySelector('#catalog-rail');
const filterGroup = document.querySelector('#catalog-filters');
const visibleCount = document.querySelector('[data-visible-count]');
const collectionCaption = document.querySelector('[data-collection-caption]');
const feminineStage = document.querySelector('#feminine-stage');
const dialog = document.querySelector('#product-dialog');
const dialogImage = document.querySelector('#dialog-image');
const dialogName = document.querySelector('#dialog-name');
const dialogEyebrow = document.querySelector('#dialog-eyebrow');
const dialogDescription = document.querySelector('#dialog-description');
const dialogCollection = document.querySelector('#dialog-collection');
const dialogCta = document.querySelector('#dialog-cta');

let catalogProducts = [];
let renderedProducts = [];
let activeCollection = 'watches';
let activeFilter = 'todos';
let activeProductIndex = 0;
let activeFeatureIndex = 0;

const collectionSettings = {
  watches: {
    caption: 'RELÓGIOS / PRESENÇA EM MOVIMENTO',
    filters: [
      ['todos', 'Todos'],
      ['classico', 'Clássico'],
      ['esportivo', 'Esportivo'],
      ['marcante', 'Marcante'],
    ],
  },
  feminine: {
    caption: 'VERATUS FEMININO / NOVA COLEÇÃO',
    filters: [
      ['todos', 'Todas'],
      ['necklace', 'Colares'],
      ['bracelet', 'Pulseiras'],
      ['anklet', 'Tornozeleiras'],
    ],
  },
};

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

function createWhatsAppLink(productName = '', productId = '') {
  const context = campaignEntries().map(([key, value]) => `${key}=${value}`).join(' | ');
  const message = [
    productName
      ? `Olá! Vim pelo site da Veratus e tenho interesse em ${productName}.`
      : 'Olá! Vim pelo site da Veratus e quero conhecer as coleções.',
    'Gostaria de confirmar disponibilidade, valores e condições atuais.',
    `Referência da visita: ${campaignReference()}${productId ? ` | produto=${productId}` : ''}`,
    context ? `Origem da visita: ${context}` : '',
  ].filter(Boolean).join('\n');
  return `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
}

document.querySelectorAll('.purchase-link:not(#dialog-cta)').forEach((link) => {
  link.href = createWhatsAppLink();
});

function productImage(product) {
  return product.primary_image || product.image || product.images?.[0] || '';
}

function productMatches(product) {
  if (activeFilter === 'todos') return true;
  if (activeCollection === 'feminine') return product.subcategory === activeFilter;
  return (product.styles || []).includes(activeFilter);
}

function currentCollectionProducts() {
  return catalogProducts.filter((product) => product.collection === activeCollection);
}

function createProductCard(product, index) {
  const article = document.createElement('article');
  article.className = `product-card product-card--${activeCollection}`;
  article.dataset.productId = product.id;
  article.style.setProperty('--card-order', index);

  const visual = document.createElement('div');
  visual.className = 'product-visual';
  const image = document.createElement('img');
  image.src = productImage(product);
  image.alt = product.alt || `${product.name} da coleção Veratus`;
  image.loading = 'lazy';
  image.width = 1122;
  image.height = 1402;
  const number = document.createElement('span');
  number.textContent = String(index + 1).padStart(2, '0');
  visual.append(image, number);

  const info = document.createElement('div');
  info.className = 'product-info';
  const eyebrow = document.createElement('p');
  eyebrow.textContent = product.eyebrow || product.product_type || 'Veratus';
  const name = document.createElement('h3');
  name.textContent = product.name;
  const open = document.createElement('button');
  open.className = 'product-open';
  open.type = 'button';
  open.innerHTML = `${activeCollection === 'feminine' ? 'Descobrir peça' : 'Entrar no modelo'} <b aria-hidden="true">↗</b>`;
  open.addEventListener('click', () => openProduct(product));
  info.append(eyebrow, name, open);
  article.append(visual, info);

  if (!reducedMotion && window.matchMedia('(pointer: fine)').matches) {
    article.addEventListener('pointermove', (event) => {
      const bounds = article.getBoundingClientRect();
      article.style.setProperty('--tilt-x', `${((event.clientY - bounds.top) / bounds.height - 0.5) * -2.2}deg`);
      article.style.setProperty('--tilt-y', `${((event.clientX - bounds.left) / bounds.width - 0.5) * 2.2}deg`);
    });
    article.addEventListener('pointerleave', () => {
      article.style.removeProperty('--tilt-x');
      article.style.removeProperty('--tilt-y');
    });
  }
  return article;
}

function renderFilters() {
  filterGroup.replaceChildren();
  for (const [value, label] of collectionSettings[activeCollection].filters) {
    const button = document.createElement('button');
    button.className = `style-button${value === activeFilter ? ' is-active' : ''}`;
    button.type = 'button';
    button.dataset.filter = value;
    button.setAttribute('aria-pressed', String(value === activeFilter));
    button.textContent = label;
    button.addEventListener('click', () => {
      activeFilter = value;
      renderCatalog();
    });
    filterGroup.append(button);
  }
}

function renderCatalog() {
  catalogSection.dataset.collection = activeCollection;
  document.querySelectorAll('.collection-button').forEach((button) => {
    const selected = button.dataset.collection === activeCollection;
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  renderFilters();
  renderedProducts = currentCollectionProducts().filter(productMatches);
  catalogGrid.replaceChildren(...renderedProducts.map(createProductCard));
  visibleCount.textContent = String(renderedProducts.length).padStart(2, '0');
  collectionCaption.textContent = collectionSettings[activeCollection].caption;
  feminineStage.hidden = activeCollection !== 'feminine';
  if (activeCollection === 'feminine') {
    activeFeatureIndex = 0;
    renderFeminineFeature();
  }
}

function setCollection(collection) {
  if (!collectionSettings[collection]) return;
  activeCollection = collection;
  activeFilter = 'todos';
  renderCatalog();
}

document.querySelectorAll('.collection-button').forEach((button) => {
  button.addEventListener('click', () => setCollection(button.dataset.collection));
});

document.querySelectorAll('[data-nav-collection]').forEach((link) => {
  link.addEventListener('click', () => setCollection(link.dataset.navCollection));
});

function openProduct(product) {
  const pool = renderedProducts.length ? renderedProducts : currentCollectionProducts();
  activeProductIndex = Math.max(0, pool.findIndex((item) => item.id === product.id));
  dialogImage.src = productImage(product);
  dialogImage.alt = product.alt || `${product.name} da coleção Veratus`;
  dialogName.textContent = product.name;
  dialogEyebrow.textContent = product.eyebrow || product.product_type || 'Veratus';
  dialogDescription.textContent = product.description || product.short_description || '';
  dialogCollection.textContent = product.collection === 'feminine' ? 'FEMININO' : 'RELÓGIOS';
  dialogCta.href = createWhatsAppLink(product.name, product.id);
  dialog.showModal();
  document.body.classList.add('dialog-open');
}

function showProduct(offset) {
  const pool = renderedProducts.length ? renderedProducts : currentCollectionProducts();
  if (!pool.length) return;
  activeProductIndex = (activeProductIndex + offset + pool.length) % pool.length;
  openProduct(pool[activeProductIndex]);
}

dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.querySelector('.dialog-next').addEventListener('click', () => showProduct(1));
dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
dialog.addEventListener('close', () => document.body.classList.remove('dialog-open'));

function renderFeminineFeature() {
  const products = currentCollectionProducts();
  if (!products.length) return;
  activeFeatureIndex = (activeFeatureIndex + products.length) % products.length;
  const product = products[activeFeatureIndex];
  const image = feminineStage.querySelector('[data-feature-image]');
  feminineStage.classList.add('is-changing');
  window.setTimeout(() => {
    image.src = productImage(product);
    image.alt = product.alt || product.name;
    feminineStage.querySelector('[data-feature-index]').textContent = String(activeFeatureIndex + 1).padStart(2, '0');
    feminineStage.querySelector('[data-feature-name]').textContent = product.name;
    feminineStage.querySelector('[data-feature-description]').textContent = product.short_description || product.description;
    feminineStage.querySelector('[data-feature-count]').textContent = `${String(activeFeatureIndex + 1).padStart(2, '0')} / ${String(products.length).padStart(2, '0')}`;
    feminineStage.classList.remove('is-changing');
  }, reducedMotion ? 0 : 170);
}

feminineStage.querySelector('[data-feature-prev]').addEventListener('click', () => {
  activeFeatureIndex -= 1;
  renderFeminineFeature();
});
feminineStage.querySelector('[data-feature-next]').addEventListener('click', () => {
  activeFeatureIndex += 1;
  renderFeminineFeature();
});
feminineStage.querySelector('[data-feature-open]').addEventListener('click', () => {
  const products = currentCollectionProducts();
  if (products[activeFeatureIndex]) openProduct(products[activeFeatureIndex]);
});

async function loadCatalog() {
  for (const endpoint of ['/api/catalog', 'catalog.json']) {
    try {
      const response = await fetch(endpoint, { headers: { Accept: 'application/json' } });
      if (!response.ok) continue;
      const payload = await response.json();
      if (!Array.isArray(payload) || !payload.length) continue;
      catalogProducts = payload;
      renderCatalog();
      return;
    } catch (_) {
      // The static projection is attempted after an unavailable API.
    }
  }
  catalogGrid.innerHTML = '<p class="catalog-loading">A coleção não pôde ser carregada agora.</p>';
}

const inspectionContent = {
  dial: { index: '01', label: 'Mostrador', title: 'A presença começa no mostrador.', copy: 'Índices, ponteiros e contraste definem a leitura visual do modelo.' },
  bezel: { index: '02', label: 'Aro', title: 'O contorno cria profundidade.', copy: 'O aro conduz o olhar e organiza as camadas da composição.' },
  band: { index: '03', label: 'Pulseira', title: 'O desenho continua no pulso.', copy: 'A pulseira completa a silhueta e conecta o relógio ao seu estilo.' },
};
const hotspots = [...document.querySelectorAll('.hotspot')];
hotspots.forEach((button) => button.addEventListener('click', () => {
  const content = inspectionContent[button.dataset.hotspot];
  hotspots.forEach((item) => {
    const active = item === button;
    item.classList.toggle('is-active', active);
    item.setAttribute('aria-expanded', String(active));
  });
  document.querySelector('[data-inspection-index]').textContent = content.index;
  document.querySelector('[data-inspection-label]').textContent = content.label;
  document.querySelector('[data-inspection-title]').textContent = content.title;
  document.querySelector('[data-inspection-copy]').textContent = content.copy;
}));

const intro = document.querySelector('#intro-gate');
let introSeen = false;
try { introSeen = sessionStorage.getItem('veratus-intro-seen') === '1'; } catch (_) { /* storage unavailable */ }
function dismissIntro() {
  if (intro.classList.contains('is-exiting')) return;
  intro.classList.add('is-exiting');
  try { sessionStorage.setItem('veratus-intro-seen', '1'); } catch (_) { /* storage unavailable */ }
}
if (introSeen || reducedMotion) dismissIntro();
else window.addEventListener('load', () => window.setTimeout(dismissIntro, 850), { once: true });
window.setTimeout(dismissIntro, 2600);

function renderTime() {
  const now = new Date();
  document.querySelectorAll('[data-live-time]').forEach((time) => {
    time.textContent = now.toLocaleTimeString('pt-BR', { hour12: false });
    time.dateTime = now.toISOString();
  });
  document.querySelectorAll('[data-watch-time]').forEach((time) => {
    time.textContent = now.toLocaleTimeString('pt-BR', { hour12: false, timeZone: 'America/Sao_Paulo' });
    time.dateTime = now.toISOString();
  });
}
renderTime();
window.setInterval(() => { if (!document.hidden) renderTime(); }, 1000);

const hourHand = document.querySelector('.time-hand--hour');
const minuteHand = document.querySelector('.time-hand--minute');
const secondHand = document.querySelector('.time-hand--second');
let watchAnimationFrame = null;
function renderWatchHands() {
  const now = new Date();
  const seconds = now.getSeconds() + now.getMilliseconds() / 1000;
  const minutes = now.getMinutes() + seconds / 60;
  const hours = (now.getHours() % 12) + minutes / 60;
  hourHand?.style.setProperty('--rotation', `${hours * 30}deg`);
  minuteHand?.style.setProperty('--rotation', `${minutes * 6}deg`);
  secondHand?.style.setProperty('--rotation', `${seconds * 6}deg`);
  if (!reducedMotion && !document.hidden) watchAnimationFrame = window.requestAnimationFrame(renderWatchHands);
}
renderWatchHands();
document.addEventListener('visibilitychange', () => {
  if (document.hidden && watchAnimationFrame) window.cancelAnimationFrame(watchAnimationFrame);
  if (!document.hidden && !reducedMotion) renderWatchHands();
});

const heroVideo = document.querySelector('.hero-video');
const heroVideoControl = document.querySelector('.hero-video-control');
heroVideoControl?.addEventListener('click', async () => {
  if (heroVideo.paused) {
    await heroVideo.play();
    heroVideoControl.textContent = 'Pausar filme';
    heroVideoControl.setAttribute('aria-pressed', 'false');
  } else {
    heroVideo.pause();
    heroVideoControl.textContent = 'Reproduzir filme';
    heroVideoControl.setAttribute('aria-pressed', 'true');
  }
});

const header = document.querySelector('.site-header');
const progress = document.querySelector('.scroll-progress span');
const menuButton = document.querySelector('.menu-toggle');
const menu = document.querySelector('.main-nav');
const mobileCta = document.querySelector('#mobile-cta');
function closeMenu() {
  menu.classList.remove('is-open');
  menuButton.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('menu-open');
}
menuButton.addEventListener('click', () => {
  const open = !menu.classList.contains('is-open');
  menu.classList.toggle('is-open', open);
  menuButton.setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('menu-open', open);
});
menu.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu));

function updateScrollEffects() {
  const y = window.scrollY;
  const scrollable = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
  header.classList.toggle('is-scrolled', y > 30);
  progress.style.transform = `scaleX(${Math.min(y / scrollable, 1)})`;
  mobileCta.classList.toggle('is-visible', y > document.querySelector('#hero').offsetHeight - 100);
}
window.addEventListener('scroll', updateScrollEffects, { passive: true });
document.querySelector('#year').textContent = new Date().getFullYear();
updateScrollEffects();
loadCatalog();
