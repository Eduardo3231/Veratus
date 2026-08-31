const options = document.querySelectorAll('.model-option');
const selectedImage = document.querySelector('#selected-model-image');
const selectedName = document.querySelector('#selected-model-name');
const selectedType = document.querySelector('#selected-model-type');
const selectedIndex = document.querySelector('#selected-index');
const purchaseLinks = document.querySelectorAll('.purchase-link');

function selectModel(option) {
  options.forEach((item) => {
    const active = item === option;
    item.classList.toggle('is-active', active);
    item.setAttribute('aria-selected', String(active));
  });

  selectedImage.classList.add('is-changing');
  window.setTimeout(() => {
    selectedImage.src = option.dataset.modelImage;
    selectedImage.alt = option.dataset.modelAlt;
    selectedName.textContent = option.dataset.modelName;
    selectedType.textContent = option.dataset.modelType;
    selectedIndex.textContent = option.dataset.modelIndex;
    selectedImage.classList.remove('is-changing');
  }, 150);

  const text = `Olá, vim pelo site da Veratus e quero saber sobre o modelo ${option.dataset.modelName}. Pode me confirmar a disponibilidade?`;
  purchaseLinks.forEach((link) => {
    link.href = `https://wa.me/5511958323612?text=${encodeURIComponent(text)}`;
  });
}

options.forEach((option) => option.addEventListener('click', () => selectModel(option)));

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const revealItems = document.querySelectorAll('.reveal');
const hero = document.querySelector('.hero');

document.body.classList.add('js-ready');
revealItems.forEach((item) => {
  if (item.dataset.revealDelay) item.style.setProperty('--reveal-delay', `${item.dataset.revealDelay}ms`);
});

function reveal(item) {
  item.classList.add('is-visible');
}

if (reducedMotion || !('IntersectionObserver' in window)) {
  revealItems.forEach(reveal);
  hero?.classList.add('is-loaded');
} else {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      reveal(entry.target);
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -35px' });

  revealItems.forEach((item) => observer.observe(item));
  window.requestAnimationFrame(() => hero?.classList.add('is-loaded'));
}
