// Veratus, recriação da landing. Dois comportamentos, os mesmos do site.js:
// a nav fixa ganha fundo depois da primeira rolagem, e o trilho de relógios
// do celular acompanha o foco do teclado.
const nav = document.querySelector('[data-nav]');
const movimentoReduzido = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function atualizarNav() {
  nav.classList.toggle('is-rolado', window.scrollY > 30);
}

window.addEventListener('scroll', atualizarNav, { passive: true });
atualizarNav();

// Com o snap obrigatório, o Tab levava o foco ao card seguinte enquanto o trilho
// voltava ao anterior, e o link focado ficava meio fora da tela.
document.querySelectorAll('.relogios .grupo__lista').forEach((trilho) => {
  trilho.addEventListener('focusin', (evento) => {
    const card = evento.target.closest('.card');
    if (!card || trilho.scrollWidth <= trilho.clientWidth) return;
    const folga = parseFloat(getComputedStyle(trilho).scrollPaddingLeft) || 0;
    const left = trilho.scrollLeft + card.getBoundingClientRect().left - trilho.getBoundingClientRect().left - folga;
    trilho.scrollTo({ left, behavior: movimentoReduzido ? 'auto' : 'smooth' });
  });
});
