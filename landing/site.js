const phone = '5511958323612';
const html = document.documentElement;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const models = {
  silver: {
    name: 'Silver',
    code: '01',
    image: 'assets/products/interactive-silver.webp',
    alt: 'Modelo Silver com interface de hora real sobreposta',
  },
  navy: {
    name: 'Navy Gold',
    code: '02',
    image: 'assets/products/interactive-navy.webp',
    alt: 'Modelo Navy Gold com interface de hora real sobreposta',
  },
  emerald: {
    name: 'Emerald',
    code: '03',
    image: 'assets/products/interactive-emerald.webp',
    alt: 'Modelo Emerald com interface de hora real sobreposta',
  },
};
const modelKeys = Object.keys(models);
let currentModelKey = 'silver';

function campaignContext() {
  const params = new URLSearchParams(window.location.search);
  return [...params.entries()]
    .filter(([key]) => key.toLowerCase().startsWith('utm_'))
    .map(([key, value]) => `${key}=${value}`)
    .join(' | ');
}

function createWhatsAppLink(productName = models[currentModelKey].name) {
  const context = campaignContext();
  const message = [
    `Olá! Vim pelo site da Veratus e quero consultar o modelo ${productName}.`,
    'Gostaria de confirmar disponibilidade, valores e condições atuais.',
    context ? `Origem da visita: ${context}` : '',
  ].filter(Boolean).join('\n');
  return `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
}

function updatePurchaseLinks() {
  document.querySelectorAll('.purchase-link:not(#dialog-cta)').forEach((link) => {
    link.href = createWhatsAppLink();
  });
}

const heroImage = document.querySelector('#hero-model-image');
const inspectionImage = document.querySelector('#inspection-image');
const modelOptions = [...document.querySelectorAll('.model-option')];

function applyModel(key, { updateUrl = true } = {}) {
  const model = models[key];
  if (!model) return;
  currentModelKey = key;
  html.dataset.model = key;
  heroImage.src = model.image;
  heroImage.alt = model.alt;
  inspectionImage.src = model.image;
  inspectionImage.alt = `${model.name} para inspeção visual`;
  document.querySelectorAll('[data-selected-model]').forEach((item) => { item.textContent = model.name; });
  document.querySelectorAll('[data-model-code]').forEach((item) => { item.textContent = model.code; });
  modelOptions.forEach((option) => {
    const active = option.dataset.modelKey === key;
    option.classList.toggle('is-active', active);
    option.setAttribute('aria-checked', String(active));
    option.tabIndex = active ? 0 : -1;
  });
  updatePurchaseLinks();
  if (updateUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set('modelo', key);
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  }
}

function selectModel(key, options = {}) {
  const change = () => applyModel(key, options);
  if (!reducedMotion && document.startViewTransition && options.transition !== false) {
    document.startViewTransition(change);
  } else {
    change();
  }
}

modelOptions.forEach((option) => {
  option.addEventListener('click', () => selectModel(option.dataset.modelKey));
  option.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const index = modelKeys.indexOf(currentModelKey);
    const nextKey = modelKeys[(index + direction + modelKeys.length) % modelKeys.length];
    selectModel(nextKey);
    document.querySelector(`[data-model-key="${nextKey}"]`)?.focus();
  });
});

Object.values(models).forEach(({ image }) => { const preload = new Image(); preload.src = image; });
const requestedModel = new URLSearchParams(window.location.search).get('modelo');
applyModel(models[requestedModel] ? requestedModel : 'silver', { updateUrl: false });

// Mostrador vivo: hora real com pausa fora da tela e em abas inativas.
const hourHand = document.querySelector('#clock-hour');
const minuteHand = document.querySelector('#clock-minute');
const secondHand = document.querySelector('#clock-second');
const liveTimes = () => document.querySelectorAll('[data-live-time]');
let clockFrame = 0;
let heroVisible = true;

function renderClock(now = new Date()) {
  const milliseconds = now.getMilliseconds();
  const seconds = now.getSeconds() + milliseconds / 1000;
  const minutes = now.getMinutes() + seconds / 60;
  const hours = (now.getHours() % 12) + minutes / 60;
  hourHand.style.transform = `rotate(${hours * 30}deg)`;
  minuteHand.style.transform = `rotate(${minutes * 6}deg)`;
  secondHand.style.transform = `rotate(${seconds * 6}deg)`;
  const value = now.toLocaleTimeString('pt-BR', { hour12: false });
  liveTimes().forEach((time) => {
    time.textContent = value;
    time.dateTime = now.toISOString();
  });
}

function clockLoop() {
  renderClock();
  if (!reducedMotion && heroVisible && !document.hidden) clockFrame = requestAnimationFrame(clockLoop);
}

function restartClock() {
  cancelAnimationFrame(clockFrame);
  renderClock();
  if (!reducedMotion && heroVisible && !document.hidden) clockFrame = requestAnimationFrame(clockLoop);
}

document.addEventListener('visibilitychange', restartClock);
const hero = document.querySelector('#hero');
const mobileCta = document.querySelector('#mobile-cta');
if ('IntersectionObserver' in window) {
  const heroObserver = new IntersectionObserver(([entry]) => {
    heroVisible = entry.isIntersecting;
    mobileCta?.classList.toggle('is-visible', !entry.isIntersecting && window.scrollY > 100);
    restartClock();
  }, { threshold: .08 });
  heroObserver.observe(hero);
}
restartClock();

const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Hora local';
document.querySelector('[data-zone]').textContent = zone.replace('_', ' ');

// Entrada curta, exibida apenas uma vez por sessão.
const introGate = document.querySelector('#intro-gate');
let introSeen = false;
try { introSeen = sessionStorage.getItem('veratus-intro-seen') === '1'; } catch (_) { introSeen = false; }
function dismissIntro() {
  if (!introGate || introGate.classList.contains('is-exiting')) return;
  introGate.classList.add('is-exiting');
  try { sessionStorage.setItem('veratus-intro-seen', '1'); } catch (_) { /* navegação privada */ }
}
if (introSeen || reducedMotion) dismissIntro();
else window.addEventListener('load', () => window.setTimeout(dismissIntro, 850), { once: true });
window.setTimeout(dismissIntro, 2600);

// Inclinação responde ao cursor, mantendo a interface estável no toque.
const heroInstrument = document.querySelector('#hero-instrument');
const heroDevice = document.querySelector('#hero-device');
if (!reducedMotion && window.matchMedia('(pointer: fine)').matches) {
  heroInstrument.addEventListener('pointermove', (event) => {
    const rect = heroInstrument.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width - .5;
    const y = (event.clientY - rect.top) / rect.height - .5;
    heroDevice.style.setProperty('--tilt-x', `${x * 7}deg`);
    heroDevice.style.setProperty('--tilt-y', `${y * -5}deg`);
  });
  heroInstrument.addEventListener('pointerleave', () => {
    heroDevice.style.setProperty('--tilt-x', '0deg');
    heroDevice.style.setProperty('--tilt-y', '0deg');
  });
}

// Narrativa em três cenas guiada pelo scroll.
const story = document.querySelector('.scroll-story');
const storyFrames = [...document.querySelectorAll('[data-story-frame]')];
const storyCopies = [...document.querySelectorAll('[data-story-copy]')];
const storyDots = [...document.querySelectorAll('.story-meter i')];
const storyCounter = document.querySelector('[data-story-counter]');
const storyGhost = document.querySelector('[data-story-ghost]');
const storyMeter = document.querySelector('.story-meter');
const storyModelKeys = ['silver', 'navy', 'emerald'];
const storyNames = ['SILVER', 'NAVY', 'EMERALD'];
let activeStory = -1;

function setStoryScene(index) {
  if (activeStory === index) return;
  activeStory = index;
  storyFrames.forEach((frame, itemIndex) => frame.classList.toggle('is-active', itemIndex === index));
  storyCopies.forEach((copy, itemIndex) => copy.classList.toggle('is-active', itemIndex === index));
  storyDots.forEach((dot, itemIndex) => dot.classList.toggle('is-active', itemIndex === index));
  storyCounter.textContent = `0${index + 1} — 03`;
  storyGhost.textContent = storyNames[index];
  selectModel(storyModelKeys[index], { transition: false, updateUrl: false });
}
storyDots[0]?.classList.add('is-active');

// Inspeção visual com conteúdo verificável.
const inspectionContent = {
  dial: { index: '01', label: 'Mostrador', title: 'A cor muda a leitura.', copy: 'O mostrador concentra o contraste principal e define a primeira impressão do modelo selecionado.' },
  bezel: { index: '02', label: 'Aro', title: 'O contorno define a presença.', copy: 'Observe como a relação visual entre aro e mostrador muda o equilíbrio de cada composição.' },
  band: { index: '03', label: 'Pulseira', title: 'A silhueta completa o estilo.', copy: 'A pulseira altera a leitura do conjunto entre clássico, esportivo e marcante.' },
};
const hotspots = [...document.querySelectorAll('.hotspot')];
function selectHotspot(key) {
  const content = inspectionContent[key];
  if (!content) return;
  hotspots.forEach((button) => {
    const active = button.dataset.hotspot === key;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-expanded', String(active));
  });
  document.querySelector('[data-inspection-index]').textContent = content.index;
  document.querySelector('[data-inspection-label]').textContent = content.label;
  document.querySelector('[data-inspection-title]').textContent = content.title;
  document.querySelector('[data-inspection-copy]').textContent = content.copy;
}
hotspots.forEach((button) => button.addEventListener('click', () => selectHotspot(button.dataset.hotspot)));

// Catálogo cinético: filtro, arraste, teclado e progresso.
const rail = document.querySelector('#catalog-rail');
const productCards = [...document.querySelectorAll('.product-card')];
const visibleCount = document.querySelector('[data-visible-count]');
const railProgress = document.querySelector('.rail-progress');

function updateRailProgress() {
  const maximum = Math.max(rail.scrollWidth - rail.clientWidth, 1);
  const ratio = Math.min(Math.max(rail.scrollLeft / maximum, 0), 1);
  railProgress?.style.setProperty('--rail-scale', String(.15 + ratio * .85));
}

document.querySelectorAll('.style-button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.style-button').forEach((item) => item.classList.toggle('is-active', item === button));
    const filter = button.dataset.filter;
    let count = 0;
    productCards.forEach((card) => {
      const visible = filter === 'todos' || card.dataset.style.split(' ').includes(filter);
      card.classList.toggle('is-hidden', !visible);
      if (visible) count += 1;
    });
    visibleCount.textContent = String(count).padStart(2, '0');
    rail.scrollTo({ left: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
    updateRailProgress();
  });
});

let dragging = false;
let dragStartX = 0;
let dragStartScroll = 0;
rail.addEventListener('pointerdown', (event) => {
  if (event.target.closest('button')) return;
  dragging = true;
  dragStartX = event.clientX;
  dragStartScroll = rail.scrollLeft;
  rail.classList.add('is-dragging');
  rail.setPointerCapture(event.pointerId);
});
rail.addEventListener('pointermove', (event) => {
  if (!dragging) return;
  rail.scrollLeft = dragStartScroll - (event.clientX - dragStartX) * 1.25;
});
function stopDragging() { dragging = false; rail.classList.remove('is-dragging'); }
rail.addEventListener('pointerup', stopDragging);
rail.addEventListener('pointercancel', stopDragging);
rail.addEventListener('scroll', updateRailProgress, { passive: true });
rail.addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  rail.scrollBy({ left: rail.clientWidth * (event.key === 'ArrowRight' ? .75 : -.75), behavior: reducedMotion ? 'auto' : 'smooth' });
});
updateRailProgress();

// Visualização imersiva dos nove modelos.
const productDialog = document.querySelector('#product-dialog');
const dialogImage = document.querySelector('#dialog-image');
const dialogName = document.querySelector('#dialog-name');
const dialogEyebrow = document.querySelector('#dialog-eyebrow');
const dialogDescription = document.querySelector('#dialog-description');
const dialogCta = document.querySelector('#dialog-cta');
let activeProductIndex = 0;

function showProduct(index) {
  const visibleCards = productCards.filter((card) => !card.classList.contains('is-hidden'));
  const normalizedIndex = (index + visibleCards.length) % visibleCards.length;
  const card = visibleCards[normalizedIndex];
  if (!card) return;
  activeProductIndex = normalizedIndex;
  dialogImage.src = card.dataset.image;
  dialogImage.alt = card.dataset.alt;
  dialogName.textContent = card.dataset.name;
  dialogEyebrow.textContent = card.dataset.eyebrow;
  dialogDescription.textContent = card.dataset.description;
  dialogCta.href = createWhatsAppLink(card.dataset.name);
  if (!reducedMotion && dialogImage.animate) {
    dialogImage.animate([{ opacity: .35, transform: 'scale(1.015)' }, { opacity: 1, transform: 'scale(1)' }], { duration: 420, easing: 'cubic-bezier(.2,.75,.25,1)' });
  }
}

productCards.forEach((card) => {
  card.querySelector('.product-open')?.addEventListener('click', () => {
    const visibleCards = productCards.filter((item) => !item.classList.contains('is-hidden'));
    showProduct(visibleCards.indexOf(card));
    productDialog.showModal();
    document.body.classList.add('dialog-open');
  });
});
productDialog.querySelector('.dialog-close').addEventListener('click', () => productDialog.close());
productDialog.querySelector('.dialog-next').addEventListener('click', () => showProduct(activeProductIndex + 1));
productDialog.addEventListener('click', (event) => { if (event.target === productDialog) productDialog.close(); });
productDialog.addEventListener('close', () => document.body.classList.remove('dialog-open'));

// Navegação e efeitos globais.
const header = document.querySelector('.site-header');
const scrollProgress = document.querySelector('.scroll-progress span');
const menuButton = document.querySelector('.menu-toggle');
const menu = document.querySelector('.main-nav');

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

let scrollTicking = false;
function updateScrollEffects() {
  const y = window.scrollY;
  const scrollable = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
  header.classList.toggle('is-scrolled', y > 30);
  scrollProgress.style.transform = `scaleX(${Math.min(y / scrollable, 1)})`;
  if (!reducedMotion) {
    const rect = story.getBoundingClientRect();
    const distance = Math.max(rect.height - window.innerHeight, 1);
    const progress = Math.min(Math.max(-rect.top / distance, 0), 1);
    if (rect.bottom > 0 && rect.top < window.innerHeight) {
      const index = Math.min(2, Math.floor(progress * 3));
      setStoryScene(index);
      storyMeter.style.setProperty('--story-progress', `${progress * 100}%`);
    }
  }
  scrollTicking = false;
}
window.addEventListener('scroll', () => {
  if (scrollTicking) return;
  scrollTicking = true;
  requestAnimationFrame(updateScrollEffects);
}, { passive: true });

document.querySelector('#year').textContent = new Date().getFullYear();
updatePurchaseLinks();
updateScrollEffects();
