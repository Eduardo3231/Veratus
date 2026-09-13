const phone = '5511958323612';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const header = document.querySelector('.site-header');
const menuButton = document.querySelector('.menu-toggle');
const menu = document.querySelector('.main-nav');
const heroContent = document.querySelector('.hero-content');

function createWhatsAppLink(productName = '') {
  const detail = productName ? ` o modelo ${productName}` : ' os modelos da coleção';
  const message = `Olá! Vim pelo site da Veratus e tenho interesse em${detail}. Gostaria de confirmar disponibilidade, valores e condições.`;
  return `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
}

function updatePurchaseLinks(productName = '') {
  document.querySelectorAll('.purchase-link').forEach((link) => { link.href = createWhatsAppLink(productName); });
}

function closeMenu() {
  menu?.classList.remove('is-open');
  menuButton?.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('menu-open');
}

menuButton?.addEventListener('click', () => {
  const open = !menu.classList.contains('is-open');
  menu.classList.toggle('is-open', open);
  menuButton.setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('menu-open', open);
});
menu?.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu));

const watchImage = document.querySelector('#selected-watch');
const watchIndex = document.querySelector('#watch-index');
const watchName = document.querySelector('#watch-name');
const watchBiome = document.querySelector('#watch-biome');
const watchDescription = document.querySelector('#watch-description');
const watchLink = document.querySelector('#selected-watch-link');

document.querySelectorAll('.watch-option').forEach((option) => {
  option.addEventListener('click', () => {
    document.querySelectorAll('.watch-option').forEach((item) => {
      const active = item === option;
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    watchImage.classList.add('is-changing');
    window.setTimeout(() => {
      watchImage.src = option.dataset.image;
      watchImage.alt = option.dataset.alt;
      watchIndex.textContent = option.dataset.index;
      watchName.textContent = option.dataset.name;
      watchBiome.textContent = option.dataset.biome;
      watchDescription.textContent = option.dataset.description;
      watchLink.href = createWhatsAppLink(option.dataset.name);
      watchImage.classList.remove('is-changing');
    }, reducedMotion ? 0 : 220);
  });
});

document.querySelectorAll('.style-button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.style-button').forEach((item) => item.classList.toggle('is-active', item === button));
    const filter = button.dataset.filter;
    document.querySelectorAll('.product-card').forEach((card) => {
      const visible = filter === 'todos' || card.dataset.style.split(' ').includes(filter);
      card.classList.toggle('is-hidden', !visible);
    });
  });
});

const revealItems = document.querySelectorAll('.reveal');
if (reducedMotion || !('IntersectionObserver' in window)) {
  revealItems.forEach((item) => item.classList.add('is-visible'));
} else {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { threshold: .12, rootMargin: '0px 0px -40px' });
  revealItems.forEach((item) => observer.observe(item));
}

let ticking = false;
function updateScrollEffects() {
  const y = window.scrollY;
  header?.classList.toggle('is-scrolled', y > 25);
  if (!reducedMotion && heroContent) {
    const progress = Math.min(y / Math.max(window.innerHeight, 1), 1);
    heroContent.style.opacity = String(1 - progress * .7);
    heroContent.style.transform = `translate3d(0, ${progress * -28}px, 0)`;
  }
  ticking = false;
}
window.addEventListener('scroll', () => {
  if (ticking) return;
  ticking = true;
  window.requestAnimationFrame(updateScrollEffects);
}, { passive: true });

document.querySelector('#year').textContent = new Date().getFullYear();
updatePurchaseLinks();
updateScrollEffects();
