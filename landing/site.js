const phone = '5511958323612';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const catalogCards = [...document.querySelectorAll('.product-card')];
const dialog = document.querySelector('#product-dialog');
const dialogImage = document.querySelector('#dialog-image');
const dialogName = document.querySelector('#dialog-name');
const dialogEyebrow = document.querySelector('#dialog-eyebrow');
const dialogDescription = document.querySelector('#dialog-description');
const dialogCta = document.querySelector('#dialog-cta');
let activeProductIndex = 0;

function campaignContext() {
  return [...new URLSearchParams(window.location.search).entries()]
    .filter(([key]) => key.toLowerCase().startsWith('utm_'))
    .map(([key, value]) => `${key}=${value}`)
    .join(' | ');
}

function createWhatsAppLink(productName = '') {
  const context = campaignContext();
  const message = [
    productName
      ? `Olá! Vim pelo site da Veratus e quero consultar o modelo ${productName}.`
      : 'Olá! Vim pelo site da Veratus e quero conhecer a coleção.',
    'Gostaria de confirmar disponibilidade, valores e condições atuais.',
    context ? `Origem da visita: ${context}` : '',
  ].filter(Boolean).join('\n');
  return `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
}

document.querySelectorAll('.purchase-link:not(#dialog-cta)').forEach((link) => {
  link.href = createWhatsAppLink();
});

// Filtros deixam os nove criativos em uma grade com espaço consistente.
const visibleCount = document.querySelector('[data-visible-count]');
document.querySelectorAll('.style-button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.style-button').forEach((item) => {
      item.classList.toggle('is-active', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    });
    const filter = button.dataset.filter;
    let count = 0;
    catalogCards.forEach((card) => {
      const visible = filter === 'todos' || card.dataset.style.split(' ').includes(filter);
      card.classList.toggle('is-hidden', !visible);
      if (visible) count += 1;
    });
    visibleCount.textContent = String(count).padStart(2, '0');
  });
});

function visibleCards() {
  return catalogCards.filter((card) => !card.classList.contains('is-hidden'));
}

function showProduct(index) {
  const cards = visibleCards();
  if (!cards.length) return;
  activeProductIndex = (index + cards.length) % cards.length;
  const card = cards[activeProductIndex];
  dialogImage.src = card.dataset.image;
  dialogImage.alt = card.dataset.alt;
  dialogName.textContent = card.dataset.name;
  dialogEyebrow.textContent = card.dataset.eyebrow;
  dialogDescription.textContent = card.dataset.description;
  dialogCta.href = createWhatsAppLink(card.dataset.name);
}

catalogCards.forEach((card) => {
  card.querySelector('.product-open').addEventListener('click', () => {
    showProduct(visibleCards().indexOf(card));
    dialog.showModal();
    document.body.classList.add('dialog-open');
  });
});
dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.querySelector('.dialog-next').addEventListener('click', () => showProduct(activeProductIndex + 1));
dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
dialog.addEventListener('close', () => document.body.classList.remove('dialog-open'));

// Pontos informativos sobre a imagem, sem cobrir o mostrador.
const inspectionContent = {
  dial: { index: '01', label: 'Mostrador', title: 'A presença começa no mostrador.', copy: 'Observe o desenho do mostrador e o contraste entre os índices, ponteiros e a cor escolhida. A imagem preserva os detalhes sem camadas artificiais.' },
  bezel: { index: '02', label: 'Aro', title: 'O contorno dá profundidade.', copy: 'O aro conduz o olhar ao centro e cria uma leitura em camadas, valorizando o contraste do conjunto.' },
  band: { index: '03', label: 'Pulseira', title: 'O desenho continua no pulso.', copy: 'A pulseira completa a silhueta e conecta o mostrador ao estilo do modelo.' },
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
try { introSeen = sessionStorage.getItem('veratus-intro-seen') === '1'; } catch (_) { /* armazenamento indisponível */ }
function dismissIntro() {
  if (intro.classList.contains('is-exiting')) return;
  intro.classList.add('is-exiting');
  try { sessionStorage.setItem('veratus-intro-seen', '1'); } catch (_) { /* armazenamento indisponível */ }
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
}
renderTime();
window.setInterval(() => { if (!document.hidden) renderTime(); }, 1000);

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
