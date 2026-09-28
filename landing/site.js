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
const dialogArt = document.querySelector('#dialog-art');
const dialogArtLabel = document.querySelector('#dialog-art-label');
const dialogPrice = document.querySelector('#dialog-price');

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

// Every WhatsApp link carries the visit reference, the dialog CTA included.
document.querySelectorAll('.purchase-link').forEach((link) => {
  link.href = createWhatsAppLink(link.dataset.productName, link.dataset.productId);
});

// Meta Pixel: the WhatsApp conversation is the site's conversion (tracking ADR,
// option 1). Only product id, name and the public price travel; never the message.
function trackPixel(event, data) {
  if (typeof window.fbq === 'function') window.fbq('track', event, data);
}

function pixelProduct(productId) {
  const product = catalogProducts.find((item) => item.id === productId);
  if (!product) return {};
  const data = { content_ids: [product.id], content_name: product.name, content_type: 'product' };
  const value = Number(product.price_brl);
  if (Number.isFinite(value) && value > 0) Object.assign(data, { value, currency: 'BRL' });
  return data;
}

document.addEventListener('click', (event) => {
  const link = event.target.closest?.('a[href^="https://wa.me/"]');
  if (link) trackPixel('Contact', pixelProduct(link.dataset.productId));
});

function productImage(product) {
  return product.primary_image || product.image || product.images?.[0] || '';
}

// Until the real photo of the exact item arrives, a watch is drawn in its own
// palette (Product Master) and says so; the second hand runs on São Paulo time.
const ILLUSTRATION_LABEL = 'Ilustração da cor';
const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const METALS = {
  steel: ['#f5f6f7', '#a9aeb3', '#5b5f64', '#f1ede5'],
  'two-tone': ['#f5f6f7', '#a9aeb3', '#5b5f64', '#d9b77a'],
  gold: ['#f6e3b4', '#c8a56a', '#7a5a2a', '#f3dfae'],
};

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function shade(hex, amount) {
  const channels = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
  const mixed = channels.map((value) => Math.round(amount < 0 ? value * (1 + amount) : value + (255 - value) * amount));
  return `#${mixed.map((value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function saoPauloTime() {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/Sao_Paulo', hour: 'numeric', minute: 'numeric', second: 'numeric', hourCycle: 'h23' })
    .formatToParts(new Date())
    .reduce((found, part) => ({ ...found, [part.type]: Number(part.value) }), {});
  return { hours: parts.hour % 12, minutes: parts.minute, seconds: parts.second };
}

function watchIllustration(product, context) {
  const palette = product.palette || {};
  const dial = HEX_COLOR.test(palette.dial) ? palette.dial : '#141414';
  const bezel = HEX_COLOR.test(palette.bezel) ? palette.bezel : '#1c1c1c';
  const [light, mid, dark, accent] = METALS[palette.metal] || METALS.steel;
  const sport = (product.styles || []).includes('esportivo');
  const ink = luminance(dial) > 0.55 ? '#1d1d1f' : '#f1ede5';
  const bezelInk = luminance(bezel) > 0.55 ? '#1d1d1f' : '#f1ede5';
  const id = `w-${context}-${product.id}`.replace(/[^a-z0-9-]/gi, '');
  const dialRadius = sport ? 88 : 98;
  const { hours, minutes, seconds } = saoPauloTime();

  const indices = Array.from({ length: 12 }, (_, hour) => {
    const angle = hour * 30;
    if (sport && hour === 0) return `<path d="M200 ${250 - dialRadius + 8}l-9 16h18z" fill="${ink}"/>`;
    if (sport && hour % 3 === 0) return `<rect x="196" y="${250 - dialRadius + 8}" width="8" height="20" rx="1.5" fill="${ink}" transform="rotate(${angle} 200 250)"/>`;
    if (sport) return `<circle cx="200" cy="${250 - dialRadius + 15}" r="5.5" fill="${ink}" transform="rotate(${angle} 200 250)"/>`;
    return `<rect x="197.5" y="${250 - dialRadius + 10}" width="5" height="${hour % 3 === 0 ? 22 : 15}" rx="1" fill="${accent}" transform="rotate(${angle} 200 250)"/>`;
  }).join('');
  const bezelMarks = sport
    ? Array.from({ length: 60 }, (_, minute) => (minute === 0 ? '' : `<rect x="${minute % 5 ? 199.4 : 198.5}" y="${143 + (minute % 5 ? 2 : 0)}" width="${minute % 5 ? 1.2 : 3}" height="${minute % 5 ? 5 : 9}" fill="${bezelInk}" opacity="${minute % 5 ? 0.55 : 0.9}" transform="rotate(${minute * 6} 200 250)"/>`)).join('')
      + `<path d="M200 157l-7-12h14z" fill="${accent}"/>`
    : '';
  const track = sport ? '' : Array.from({ length: 60 }, (_, minute) => `<rect x="199.6" y="${250 - dialRadius + 3}" width=".8" height="4" fill="${ink}" opacity=".45" transform="rotate(${minute * 6} 200 250)"/>`).join('');
  const links = [22, 44, 66, 88, 110, 132].map((y) => `<path d="M${152 + y * 0.05} ${y}H${248 - y * 0.05}M${152 + y * 0.05} ${500 - y}H${248 - y * 0.05}" stroke="${dark}" stroke-opacity=".38"/>`).join('');

  return `<svg class="watch-art" viewBox="0 0 400 500" role="img" aria-label="Ilustração do ${product.name} na cor do modelo">
    <defs>
      <linearGradient id="${id}-metal" x1="0" x2="1"><stop offset="0" stop-color="${dark}"/><stop offset=".28" stop-color="${light}"/><stop offset=".55" stop-color="${mid}"/><stop offset=".82" stop-color="${light}"/><stop offset="1" stop-color="${dark}"/></linearGradient>
      <linearGradient id="${id}-ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${light}"/><stop offset=".5" stop-color="${dark}"/><stop offset="1" stop-color="${light}"/></linearGradient>
      <radialGradient id="${id}-dial" cx=".38" cy=".32" r=".8"><stop offset="0" stop-color="${shade(dial, 0.22)}"/><stop offset=".55" stop-color="${dial}"/><stop offset="1" stop-color="${shade(dial, -0.45)}"/></radialGradient>
      <radialGradient id="${id}-glow" cx=".5" cy=".5" r=".55"><stop offset="0" stop-color="${dial}" stop-opacity=".42"/><stop offset="1" stop-color="${dial}" stop-opacity="0"/></radialGradient>
      <linearGradient id="${id}-fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".24" stop-color="#fff"/><stop offset=".76" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
      <mask id="${id}-mask"><rect width="400" height="500" fill="url(#${id}-fade)"/></mask>
      <clipPath id="${id}-crystal"><circle cx="200" cy="250" r="${dialRadius}"/></clipPath>
    </defs>
    <rect width="400" height="500" fill="url(#${id}-glow)"/>
    <g mask="url(#${id}-mask)">
      <path d="M150 0h100l-7 150h-86z" fill="url(#${id}-metal)"/><path d="M157 350h86l7 150H150z" fill="url(#${id}-metal)"/>
      <g stroke-width="1.4">${links}</g>
      <path d="M186 0v150M214 0v150M186 350v150M214 350v150" stroke="${dark}" stroke-opacity=".3"/>
    </g>
    <rect x="309" y="236" width="18" height="28" rx="4" fill="url(#${id}-metal)"/>
    <circle cx="200" cy="250" r="118" fill="url(#${id}-metal)"/>
    <circle cx="200" cy="250" r="110" fill="${sport ? bezel : `url(#${id}-ring)`}" stroke="${dark}" stroke-opacity=".5"/>
    ${bezelMarks}
    <circle cx="200" cy="250" r="${dialRadius}" fill="url(#${id}-dial)" stroke="${dark}" stroke-opacity=".6"/>
    ${track}${indices}
    <g class="watch-art__hand" style="--turn:${hours * 30 + minutes / 2}deg"><rect x="195.5" y="196" width="9" height="62" rx="3.5" fill="${ink}" stroke="${dark}" stroke-opacity=".35"/></g>
    <g class="watch-art__hand" style="--turn:${minutes * 6 + seconds / 10}deg"><rect x="196.5" y="${250 - dialRadius + 16}" width="7" height="${dialRadius - 8}" rx="3" fill="${ink}" stroke="${dark}" stroke-opacity=".35"/></g>
    <g class="watch-art__second" style="--turn:${seconds * 6}deg"><rect x="199" y="${250 - dialRadius + 8}" width="2" height="${dialRadius + 14}" fill="${accent}"/></g>
    <circle cx="200" cy="250" r="7" fill="${accent}" stroke="${dark}" stroke-opacity=".5"/>
    <ellipse cx="168" cy="196" rx="112" ry="54" fill="#fff" opacity=".07" transform="rotate(-32 168 196)" clip-path="url(#${id}-crystal)"/>
  </svg>`;
}

function formatPrice(product) {
  const value = Number(product.price_brl);
  return Number.isFinite(value) && value > 0
    ? value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
    : '';
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
  const photo = productImage(product);
  const number = document.createElement('span');
  number.textContent = String(index + 1).padStart(2, '0');
  if (photo) {
    const image = document.createElement('img');
    image.src = photo;
    image.alt = product.alt || `${product.name} da coleção Veratus`;
    image.loading = 'lazy';
    image.width = 1122;
    image.height = 1402;
    visual.append(image, number);
  } else {
    visual.classList.add('product-visual--art');
    visual.innerHTML = watchIllustration(product, 'card');
    const label = document.createElement('em');
    label.textContent = ILLUSTRATION_LABEL;
    visual.append(number, label);
  }
  visual.addEventListener('click', () => openProduct(product));

  const info = document.createElement('div');
  info.className = 'product-info';
  const eyebrow = document.createElement('p');
  eyebrow.textContent = product.eyebrow || product.product_type || 'Veratus';
  const name = document.createElement('h3');
  name.textContent = product.name;
  const open = document.createElement('button');
  open.className = 'product-open';
  open.type = 'button';
  open.addEventListener('click', () => openProduct(product));
  info.append(eyebrow, name);
  const price = formatPrice(product);
  if (price) {
    info.classList.add('product-info--priced');
    const priceLine = document.createElement('div');
    priceLine.className = 'product-price';
    priceLine.innerHTML = `<b></b><span>Frete grátis · até 7 dias</span>`;
    priceLine.querySelector('b').textContent = price;
    const order = document.createElement('a');
    order.className = 'product-order purchase-link';
    order.href = createWhatsAppLink(product.name, product.id, true);
    order.target = '_blank';
    order.rel = 'noopener';
    order.dataset.productId = product.id;
    order.innerHTML = 'Pedir <b aria-hidden="true">↗</b>';
    order.setAttribute('aria-label', `Pedir ${product.name} pelo WhatsApp`);
    open.innerHTML = 'Detalhes';
    const actions = document.createElement('div');
    actions.className = 'product-actions';
    actions.append(open, order);
    info.append(priceLine, actions);
  } else {
    open.innerHTML = `${activeCollection === 'feminine' ? 'Descobrir peça' : 'Entrar no modelo'} <b aria-hidden="true">↗</b>`;
    info.append(open);
  }
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
  const photo = productImage(product);
  dialogImage.hidden = !photo;
  if (photo) {
    dialogImage.src = photo;
    dialogImage.alt = product.alt || `${product.name} da coleção Veratus`;
  }
  dialogArt.innerHTML = photo ? '' : watchIllustration(product, 'dialog');
  dialogArt.hidden = Boolean(photo);
  dialogArtLabel.hidden = Boolean(photo);
  dialogImage.parentElement.classList.toggle('dialog-media--art', !photo);
  dialogName.textContent = product.name;
  dialogEyebrow.textContent = product.eyebrow || product.product_type || 'Veratus';
  dialogDescription.textContent = product.description || product.short_description || '';
  dialogCollection.textContent = product.collection === 'feminine' ? 'FEMININO' : 'RELÓGIOS';
  const price = formatPrice(product);
  dialogPrice.hidden = !price;
  dialogPrice.querySelector('b').textContent = price;
  dialogCta.href = createWhatsAppLink(product.name, product.id, Boolean(price));
  dialogCta.dataset.productId = product.id;
  dialogCta.textContent = price ? 'Pedir pelo WhatsApp' : 'Tenho interesse';
  trackPixel('ViewContent', pixelProduct(product.id));
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
  const failure = document.createElement('p');
  failure.className = 'catalog-loading';
  const fallbackLink = document.createElement('a');
  fallbackLink.className = 'text-link purchase-link';
  fallbackLink.href = createWhatsAppLink();
  fallbackLink.target = '_blank';
  fallbackLink.rel = 'noopener';
  fallbackLink.innerHTML = 'Consultar pelo WhatsApp <span aria-hidden="true">↗</span>';
  failure.append('A coleção não pôde ser carregada agora. ', fallbackLink);
  catalogGrid.replaceChildren(failure);
}

const inspectionContent = {
  precision: { index: '01', label: 'Precisão', title: 'Cada linha tem um motivo.', copy: 'O V tem traço firme e proporção exata, para ser reconhecido de perto e à distância.' },
  balance: { index: '02', label: 'Equilíbrio', title: 'Força e gesto no mesmo símbolo.', copy: 'A espiga de trigo equilibra a geometria do V com um desenho orgânico.' },
  permanence: { index: '03', label: 'Permanência', title: 'Feito para durar além da tendência.', copy: 'Poucos elementos, bem escolhidos, para que a marca continue atual com o tempo.' },
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

// The 3D brand mark follows the pointer; scrolling turns it through CSS alone.
const brand3d = document.querySelector('.brand-3d');
const brand3dTilt = brand3d?.querySelector('.brand-3d__tilt');
if (brand3dTilt && !reducedMotion && window.matchMedia('(pointer: fine)').matches) {
  brand3d.addEventListener('pointermove', (event) => {
    const bounds = brand3d.getBoundingClientRect();
    brand3dTilt.style.setProperty('--tilt-x', `${((event.clientY - bounds.top) / bounds.height - 0.5) * -12}deg`);
    brand3dTilt.style.setProperty('--tilt-y', `${((event.clientX - bounds.left) / bounds.width - 0.5) * 16}deg`);
  });
  brand3d.addEventListener('pointerleave', () => {
    brand3dTilt.style.removeProperty('--tilt-x');
    brand3dTilt.style.removeProperty('--tilt-y');
  });
}

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

const header = document.querySelector('.site-header');
const progress = document.querySelector('.scroll-progress span');
const menuButton = document.querySelector('.menu-toggle');
const menu = document.querySelector('.main-nav');
const mobileCta = document.querySelector('#mobile-cta');
const compactNav = window.matchMedia('(max-width: 980px)');
// Links hidden by opacity stay out of the tab order and the accessibility tree.
function syncMenuInert() {
  menu.inert = compactNav.matches && !menu.classList.contains('is-open');
}
function closeMenu() {
  menu.classList.remove('is-open');
  menuButton.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('menu-open');
  syncMenuInert();
}
menuButton.addEventListener('click', () => {
  const open = !menu.classList.contains('is-open');
  menu.classList.toggle('is-open', open);
  menuButton.setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('menu-open', open);
  syncMenuInert();
});
menu.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu));
compactNav.addEventListener('change', syncMenuInert);
syncMenuInert();

function updateScrollEffects() {
  const y = window.scrollY;
  const scrollable = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
  header.classList.toggle('is-scrolled', y > 30);
  progress.style.transform = `scaleX(${Math.min(y / scrollable, 1)})`;
  const ctaVisible = y > document.querySelector('#hero').offsetHeight - 100;
  mobileCta.classList.toggle('is-visible', ctaVisible);
  mobileCta.inert = !ctaVisible;
}
window.addEventListener('scroll', updateScrollEffects, { passive: true });
document.querySelector('#year').textContent = new Date().getFullYear();
updateScrollEffects();
loadCatalog();
