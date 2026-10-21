'use strict';

// Prévia da reestruturação. O HTML já traz tudo visível; este arquivo só
// acrescenta filtros, a vista do relógio (palco 3D) e as referências de visita.

const telefone = '5511958323612';
const movimentoReduzido = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---- WhatsApp: mesma mensagem e mesma referência do site do ar (landing/site.js).
const camposCampanha = new Set(['utm_source', 'utm_medium', 'utm_campaign', 'utm_content']);

function entradasCampanha() {
  return [...new URLSearchParams(window.location.search).entries()]
    .map(([chave, valor]) => [chave.toLowerCase(), valor.trim().slice(0, 80)])
    .filter(([chave, valor]) => camposCampanha.has(chave) && valor);
}

function referenciaVisita() {
  const fonte = entradasCampanha().map(([chave, valor]) => `${chave}=${valor}`).join('&') || 'site-direto';
  let hash = 2166136261;
  for (const caractere of fonte) {
    hash ^= caractere.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `VT-${(hash >>> 0).toString(36).toUpperCase()}`;
}

function linkWhatsApp(nome = '', id = '', pedido = false) {
  const origem = entradasCampanha().map(([chave, valor]) => `${chave}=${valor}`).join(' | ');
  const mensagem = [
    nome ? `Olá! Vim pelo site da Veratus e tenho interesse em ${nome}.` : 'Olá! Vim pelo site da Veratus e quero conhecer as coleções.',
    pedido ? 'Quero fazer o pedido e confirmar a disponibilidade.' : 'Gostaria de confirmar disponibilidade e valor.',
    `Referência da visita: ${referenciaVisita()}${id ? ` | produto=${id}` : ''}`,
    origem ? `Origem da visita: ${origem}` : '',
  ].filter(Boolean).join('\n');
  return `https://wa.me/${telefone}?text=${encodeURIComponent(mensagem)}`;
}

function atualizarLink(link) {
  link.href = linkWhatsApp(link.dataset.productName, link.dataset.productId, link.dataset.pedido === '1');
}
document.querySelectorAll('.purchase-link').forEach(atualizarLink);

// ---- Relógios lidos do próprio HTML (a foto é a do cartão).
const relogios = [...document.querySelectorAll('.relogio')].map((item) => ({
  item,
  id: item.dataset.id,
  nome: item.dataset.nome,
  estilo: item.dataset.estilo,
  estilos: item.dataset.estilos.split(' '),
  descricao: item.dataset.descricao,
  preco: item.dataset.preco,
  alt: item.dataset.alt,
  fx: item.dataset.fx,
  fy: item.dataset.fy,
  foto: item.querySelector('img').getAttribute('src'),
}));

// ---- Meta Pixel: só id, nome e preço público; nunca a mensagem (sem fbq, nada acontece).
function registrarPixel(evento, dados) {
  if (typeof window.fbq === 'function') window.fbq('track', evento, dados);
}

function dadosPixel(id) {
  if (!id) return {};
  const relogio = relogios.find((item) => item.id === id);
  const link = document.querySelector(`.purchase-link[data-product-id="${CSS.escape(id)}"]`);
  const dados = { content_ids: [id], content_name: relogio?.nome || link?.dataset.productName || id, content_type: 'product' };
  const valor = relogio ? Number(relogio.preco.replace(/[^\d,]/g, '').replace(',', '.')) : NaN;
  if (Number.isFinite(valor) && valor > 0) Object.assign(dados, { value: valor, currency: 'BRL' });
  return dados;
}

document.addEventListener('click', (evento) => {
  const link = evento.target.closest?.('a[href^="https://wa.me/"]');
  if (link) registrarPixel('Contact', dadosPixel(link.dataset.productId));
});

// ---- Filtros por estilo.
const filtros = document.querySelector('[data-filtros]');
const contagem = document.querySelector('[data-contagem]');
filtros.hidden = false;
filtros.addEventListener('click', (evento) => {
  const botao = evento.target.closest('[data-filtro]');
  if (!botao) return;
  const filtro = botao.dataset.filtro;
  filtros.querySelectorAll('[data-filtro]').forEach((outro) => outro.setAttribute('aria-pressed', String(outro === botao)));
  let visiveis = 0;
  for (const relogio of relogios) {
    const mostrar = filtro === 'todos' || relogio.estilos.includes(filtro);
    relogio.item.hidden = !mostrar;
    if (mostrar) visiveis += 1;
  }
  contagem.textContent = `${visiveis} ${visiveis === 1 ? 'modelo' : 'modelos'}`;
});

// ---- Topo e barra do celular.
const topo = document.querySelector('.topo');
const barraMovel = document.querySelector('[data-barra-movel]');
const hero = document.querySelector('#hero');
function aoRolar() {
  topo.classList.toggle('is-rolado', window.scrollY > 30);
  const visivel = window.scrollY > hero.offsetHeight - 120;
  barraMovel.classList.toggle('is-visivel', visivel);
  barraMovel.inert = !visivel;
}
window.addEventListener('scroll', aoRolar, { passive: true });
aoRolar();
document.querySelector('[data-ano]').textContent = String(new Date().getFullYear());

// ---- Vista do relógio. A rolagem da vista gira o relógio; o ponteiro inclina.
const vista = document.querySelector('#vista');
const palco = vista.querySelector('[data-palco]');
const fotoPalco = palco.querySelector('.palco__foto');
const ambiente = palco.querySelector('.palco__ambiente');
const listaOutros = vista.querySelector('[data-outros]');
let abriuPeloSite = false;
let ponteiro = { x: 0, y: 0 };
let quadro = 0;
let focoAnterior = null;

// Visualizadores com sandbox (arquivo local, pré-visualização) podem recusar o histórico.
function mudarHistorico(metodo, estado, url) {
  try {
    window.history[metodo](estado, '', url);
    return true;
  } catch (_) {
    return false;
  }
}

function urlCom(id) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('relogio', id);
  else url.searchParams.delete('relogio');
  url.hash = '';
  return url;
}

function miniatura(relogio) {
  const botao = document.createElement('button');
  botao.type = 'button';
  botao.dataset.trocar = relogio.id;
  const moldura = document.createElement('span');
  moldura.className = 'mini';
  moldura.style.setProperty('--fx', relogio.fx);
  moldura.style.setProperty('--fy', relogio.fy);
  const imagem = document.createElement('img');
  imagem.src = relogio.foto;
  imagem.alt = '';
  imagem.loading = 'lazy';
  moldura.append(imagem);
  const nome = document.createElement('span');
  nome.textContent = relogio.nome;
  botao.append(moldura, nome);
  const item = document.createElement('li');
  item.append(botao);
  return item;
}

function preencher(relogio) {
  fotoPalco.src = relogio.foto;
  fotoPalco.alt = relogio.alt;
  ambiente.style.backgroundImage = `url("${relogio.foto}")`;
  vista.querySelector('[data-nome]').textContent = relogio.nome;
  vista.querySelector('[data-estilo]').textContent = relogio.estilo;
  vista.querySelector('[data-descricao]').textContent = relogio.descricao;
  vista.querySelectorAll('[data-preco]').forEach((preco) => { preco.textContent = relogio.preco; });
  vista.querySelector('[data-posicao]').textContent = `${relogios.indexOf(relogio) + 1} de ${relogios.length}`;
  vista.querySelectorAll('[data-pedir]').forEach((link, indice) => {
    Object.assign(link.dataset, { productId: relogio.id, productName: relogio.nome, pedido: '1' });
    atualizarLink(link);
    link.textContent = indice === 0 ? `Pedir o ${relogio.nome} pelo WhatsApp` : 'Pedir pelo WhatsApp';
    link.setAttribute('aria-label', `Pedir o ${relogio.nome} pelo WhatsApp`);
  });
  listaOutros.replaceChildren(...relogios.filter((outro) => outro !== relogio).map(miniatura));
  registrarPixel('ViewContent', dadosPixel(relogio.id));
}

function aplicarGiro() {
  quadro = 0;
  if (movimentoReduzido) return;
  const alcance = vista.scrollHeight - vista.clientHeight;
  const progresso = alcance > 0 ? Math.min(vista.scrollTop / alcance, 1) : 0.5;
  const giro = -26 + 52 * progresso + ponteiro.x * 12;
  palco.style.setProperty('--giro', `${giro.toFixed(2)}deg`);
  palco.style.setProperty('--giro-n', giro.toFixed(2));
  palco.style.setProperty('--incl', `${(ponteiro.y * -6).toFixed(2)}deg`);
  palco.style.setProperty('--reflexo', `${(progresso * 100).toFixed(1)}%`);
  palco.style.setProperty('--mostrador', `${(progresso * 150).toFixed(1)}deg`);
}

function pedirGiro() {
  if (!quadro) quadro = window.requestAnimationFrame(aplicarGiro);
}

function entrarEmCena() {
  palco.classList.remove('is-girado', 'is-entrando');
  if (movimentoReduzido) return;
  void palco.offsetWidth; // reinicia a animação de entrada
  palco.classList.add('is-entrando');
}

function abrir(id, { empilhar = true } = {}) {
  const relogio = relogios.find((item) => item.id === id);
  if (!relogio) return;
  preencher(relogio);
  if (!vista.open) {
    focoAnterior = document.activeElement;
    vista.showModal();
    document.body.classList.add('vista-aberta');
  }
  vista.scrollTop = 0;
  entrarEmCena();
  if (empilhar) abriuPeloSite = mudarHistorico('pushState', { relogio: id }, urlCom(id));
  aplicarGiro();
}

function fechar() {
  if (!vista.open) return;
  if (abriuPeloSite && window.history.state?.relogio) {
    window.history.back(); // o popstate fecha a vista
    return;
  }
  vista.close();
}

document.addEventListener('click', (evento) => {
  const gatilho = evento.target.closest('[data-abrir]');
  if (!gatilho || evento.button !== 0 || evento.metaKey || evento.ctrlKey || evento.shiftKey) return;
  const item = gatilho.closest('.relogio');
  if (!item) return;
  evento.preventDefault();
  abrir(item.dataset.id);
});

listaOutros.addEventListener('click', (evento) => {
  const botao = evento.target.closest('[data-trocar]');
  if (!botao) return;
  abrir(botao.dataset.trocar, { empilhar: false });
  mudarHistorico('replaceState', { relogio: botao.dataset.trocar }, urlCom(botao.dataset.trocar));
  vista.querySelector('[data-fechar]').focus();
});

vista.querySelector('[data-fechar]').addEventListener('click', fechar);
vista.addEventListener('cancel', (evento) => {
  evento.preventDefault();
  fechar();
});
vista.addEventListener('close', () => {
  document.body.classList.remove('vista-aberta');
  if (new URL(window.location.href).searchParams.has('relogio')) mudarHistorico('replaceState', null, urlCom(null));
  abriuPeloSite = false;
  focoAnterior?.focus?.();
});
window.addEventListener('popstate', () => {
  const id = new URL(window.location.href).searchParams.get('relogio');
  if (id) abrir(id, { empilhar: false });
  else if (vista.open) {
    abriuPeloSite = false;
    vista.close();
  }
});

vista.addEventListener('scroll', () => {
  if (vista.scrollTop > 40) palco.classList.add('is-girado');
  pedirGiro();
}, { passive: true });

if (!movimentoReduzido && window.matchMedia('(pointer: fine)').matches) {
  palco.addEventListener('pointermove', (evento) => {
    const caixa = palco.getBoundingClientRect();
    ponteiro = { x: (evento.clientX - caixa.left) / caixa.width - 0.5, y: (evento.clientY - caixa.top) / caixa.height - 0.5 };
    pedirGiro();
  });
  palco.addEventListener('pointerleave', () => {
    ponteiro = { x: 0, y: 0 };
    pedirGiro();
  });
}

// Link direto para um modelo (anúncio, Direct, bio): ?relogio=ocean-blue
const inicial = new URL(window.location.href).searchParams.get('relogio');
if (inicial) abrir(inicial, { empilhar: false });
