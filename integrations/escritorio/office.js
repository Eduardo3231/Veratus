/* Escritório Veratus: o time de agentes desenhado como um escritório isométrico.
 *
 * O mesmo desenho serve ao painel publicado (reprodução do dia simulado) e à página
 * ao vivo do Veratus OS (/os/escritorio/painel, que lê /os/escritorio). Quem chama
 * entrega o estado de cada funcionário; aqui só se desenha. Sem rede, sem dado de
 * cliente: só nomes internos, cargos, status e o que cada um fez.
 *
 *   const office = VeratusOffice.create(canvas, { staff, onSelect });
 *   office.update({ agentes, quadro, movimentos, chamado }, { animar: true });
 *   office.select('sales-agent');
 */
(function (global) {
  'use strict';

  const TW = 60;
  const TH = 30;
  const ZH = 26;
  const FLOOR = { w: 24, d: 17 };
  const WALL = 3.4;
  const PAD = { left: 20, top: 46, right: 20, bottom: 24 };
  const OX = FLOOR.d * (TW / 2) + PAD.left;
  const OY = WALL * ZH + PAD.top;
  const WIDTH = (FLOOR.w + FLOOR.d) * (TW / 2) + PAD.left + PAD.right;
  const HEIGHT = (FLOOR.w + FLOOR.d) * (TH / 2) + WALL * ZH + PAD.top + PAD.bottom;

  const ZONES = [
    { id: 'diretoria', label: 'Diretoria', x: 0.4, y: 0.4, w: 4.6, d: 4.2, kind: 'glass' },
    { id: 'atendimento', label: 'Atendimento', x: 5.6, y: 0.4, w: 10.8, d: 4.6, kind: 'floor' },
    { id: 'fundador', label: 'Sala do fundador', x: 17.0, y: 0.4, w: 6.6, d: 4.2, kind: 'glass' },
    { id: 'marketplaces', label: 'Marketplaces', x: 0.4, y: 5.8, w: 12.6, d: 5.0, kind: 'floor' },
    { id: 'redes', label: 'Redes e mídia', x: 13.6, y: 5.8, w: 6.2, d: 5.0, kind: 'floor' },
    { id: 'expedicao', label: 'Expedição', x: 20.4, y: 5.8, w: 3.2, d: 5.0, kind: 'floor' },
    { id: 'produto', label: 'Produto', x: 0.4, y: 11.6, w: 12.6, d: 5.0, kind: 'floor' },
    { id: 'copa', label: 'Copa e reunião', x: 13.6, y: 11.6, w: 10.0, d: 5.0, kind: 'wood' },
  ];

  // Mesa de cada papel do AGENT_REGISTRY (x, y = canto da mesa; a pessoa senta atrás).
  const DESKS = {
    'general-manager': { x: 1.6, y: 2.4, monitors: 2 },
    'customer-supervisor': { x: 6.6, y: 2.6, monitors: 1 },
    'sales-agent': { x: 9.6, y: 2.6, monitors: 3, sign: 'WHATSAPP · INSTAGRAM · MESSENGER' },
    'aftersales-agent': { x: 13.0, y: 2.6, monitors: 2 },
    fundador: { x: 19.4, y: 2.4, monitors: 1 },
    'distribution-supervisor': { x: 1.4, y: 7.0, monitors: 1 },
    'meta-agent': { x: 4.4, y: 7.0, monitors: 2, sign: 'FACEBOOK' },
    'mercado-livre-agent': { x: 7.4, y: 7.0, monitors: 2, sign: 'MERCADO LIVRE' },
    'shopee-agent': { x: 10.4, y: 7.0, monitors: 2, sign: 'SHOPEE' },
    'tiktok-shop-agent': { x: 4.4, y: 9.4, monitors: 2, sign: 'TIKTOK SHOP' },
    'amazon-agent': { x: 7.4, y: 9.4, monitors: 2, sign: 'AMAZON' },
    'sync-monitor-agent': { x: 10.4, y: 9.4, monitors: 2, sign: 'ESTOQUE' },
    'social-media-worker': { x: 14.4, y: 7.4, monitors: 2, sign: 'INSTAGRAM · TIKTOK' },
    'paid-acquisition-worker': { x: 17.4, y: 7.4, monitors: 2, sign: 'MÍDIA PAGA' },
    'product-supervisor': { x: 1.4, y: 12.8, monitors: 1 },
    'product-intake': { x: 4.4, y: 12.8, monitors: 1 },
    'copy-agent': { x: 7.4, y: 12.8, monitors: 1 },
    'creative-agent': { x: 10.4, y: 12.8, monitors: 2 },
    'pricing-agent': { x: 4.4, y: 15.0, monitors: 1 },
    'quality-agent': { x: 7.4, y: 15.0, monitors: 1 },
  };
  const DESK = { w: 1.9, d: 0.9, h: 0.72 };
  const SPOTS = {
    fundador: { x: 18.6, y: 4.9 },
    expedicao: { x: 21.4, y: 8.9 },
    reuniao: { x: 16.6, y: 13.4 },
    caixa: { x: 15.4, y: 4.6 },
  };
  const MEETING_SEATS = [
    [15.4, 12.9], [16.6, 12.6], [17.8, 12.6], [19.0, 12.9],
    [15.4, 14.4], [16.6, 14.7], [17.8, 14.7], [19.0, 14.4],
  ];
  const AISLE_Y = [5.4, 11.2];
  const AISLE_X = 13.3;
  const SKIN = ['#f1c7a5', '#d9a47c', '#b07a52', '#8a5a3b', '#5e3c28', '#e8b98f'];
  const HAIR = ['#2a1d15', '#4b3426', '#8a6a4a', '#b8935a', '#1b1b1b', '#6b4a3a'];
  const FLAG = { green: '#1f8a4c', yellow: '#f2c94c', blue: '#24418f', white: '#f6f4f0' };

  // -- cores a partir dos tokens da página --------------------------------------------------
  function parseColor(value) {
    const text = String(value || '').trim();
    if (text.startsWith('#')) {
      const hex = text.length === 4 ? text.slice(1).split('').map((c) => c + c).join('') : text.slice(1, 7);
      return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16), 1];
    }
    const match = text.match(/rgba?\(([^)]+)\)/);
    if (match) {
      const parts = match[1].split(',').map((p) => parseFloat(p));
      return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
    }
    return [128, 128, 128, 1];
  }
  const css = (rgba) => `rgba(${Math.round(rgba[0])}, ${Math.round(rgba[1])}, ${Math.round(rgba[2])}, ${rgba[3]})`;
  function mix(a, b, t) {
    const x = typeof a === 'string' ? parseColor(a) : a;
    const y = typeof b === 'string' ? parseColor(b) : b;
    return [x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, x[2] + (y[2] - x[2]) * t, 1];
  }
  function solid(value, ground) {
    const c = parseColor(value);
    return c[3] >= 1 ? c : mix(ground, [c[0], c[1], c[2], 1], c[3]);
  }

  function readPalette(element) {
    const style = getComputedStyle(element);
    const get = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    const bg = parseColor(get('--bg', '#0d0d0d'));
    const fg = parseColor(get('--fg', '#f6f4f0'));
    const surface = solid(get('--surface', '#151412'), bg);
    const surface2 = solid(get('--surface-2', '#1d1b18'), bg);
    const gold = solid(get('--gold', '#c8a56a'), bg);
    const dark = (bg[0] + bg[1] + bg[2]) / 3 < 128;
    const deskTop = dark ? mix(fg, bg, 0.1) : mix(bg, fg, 0.06);
    return {
      dark,
      bg: css(bg),
      fg: css(fg),
      muted: get('--muted', '#a39e95'),
      line: get('--line', 'rgba(246,244,240,0.12)'),
      lineStrong: get('--line-strong', 'rgba(246,244,240,0.22)'),
      gold: css(gold),
      goldSoft: get('--gold-soft', 'rgba(200,165,106,0.14)'),
      onGold: get('--on-gold', '#141311'),
      ok: get('--ok', '#86b892'),
      warn: get('--warn', '#dcae52'),
      alert: get('--alert', '#e0836e'),
      floor: css(dark ? mix(surface2, bg, 0.2) : mix(surface2, bg, 0.3)),
      floorZone: css(dark ? mix(surface2, fg, 0.03) : mix(surface2, fg, 0.03)),
      wood: css(mix(surface2, gold, dark ? 0.32 : 0.42)),
      woodLine: css(mix(surface2, gold, dark ? 0.2 : 0.55)),
      wall: css(dark ? mix(surface, fg, 0.04) : mix(surface, fg, 0.04)),
      wallSide: css(dark ? mix(surface, bg, 0.35) : mix(surface, fg, 0.09)),
      wallTop: css(dark ? mix(surface, fg, 0.16) : mix(surface, fg, 0.18)),
      deskTop: css(deskTop),
      deskLeft: css(mix(deskTop, dark ? bg : fg, dark ? 0.28 : 0.12)),
      deskRight: css(mix(deskTop, dark ? bg : fg, dark ? 0.45 : 0.2)),
      chair: css(dark ? mix(bg, fg, 0.16) : mix(fg, bg, 0.35)),
      shirt: css(dark ? mix(fg, gold, 0.06) : mix(bg, fg, 0.02)),
      shirtEdge: css(dark ? mix(fg, bg, 0.3) : mix(bg, fg, 0.22)),
      pants: css(dark ? mix(bg, fg, 0.22) : mix(fg, bg, 0.2)),
      monitor: css(dark ? mix(bg, fg, 0.08) : mix(fg, bg, 0.18)),
      glass: dark ? 'rgba(246, 244, 240, 0.05)' : 'rgba(13, 13, 13, 0.04)',
      glassEdge: dark ? 'rgba(246, 244, 240, 0.28)' : 'rgba(13, 13, 13, 0.25)',
      plant: css(mix(parseColor(get('--ok', '#86b892')), bg, dark ? 0.15 : 0.05)),
      pot: css(dark ? mix(surface, fg, 0.25) : mix(surface, fg, 0.15)),
      screen: css(dark ? mix(bg, [0, 0, 0, 1], 0.4) : mix(fg, bg, 0.05)),
      screenFg: dark ? css(fg) : css(bg),
    };
  }

  // -- geometria isométrica ------------------------------------------------------------------
  const iso = (gx, gy, z) => [OX + (gx - gy) * (TW / 2), OY + (gx + gy) * (TH / 2) - (z || 0) * ZH];

  function poly(ctx, points, fill, stroke) {
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
  }

  function box(ctx, x, y, w, d, z0, h, colors) {
    const z1 = z0 + h;
    poly(ctx, [iso(x, y + d, z0), iso(x + w, y + d, z0), iso(x + w, y + d, z1), iso(x, y + d, z1)], colors.left, colors.edge);
    poly(ctx, [iso(x + w, y, z0), iso(x + w, y + d, z0), iso(x + w, y + d, z1), iso(x + w, y, z1)], colors.right, colors.edge);
    poly(ctx, [iso(x, y, z1), iso(x + w, y, z1), iso(x + w, y + d, z1), iso(x, y + d, z1)], colors.top, colors.edge);
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // Plano da parede do fundo (y = 0) e da parede da esquerda (x = 0), em pixels de tela.
  function onBackWall(ctx, scale, gx, z, draw) {
    const [ex, ey] = iso(gx, 0, z);
    ctx.save();
    ctx.setTransform(scale, scale * 0.5, 0, scale, ex * scale, ey * scale);
    draw();
    ctx.restore();
  }
  function onLeftWall(ctx, scale, gy, z, draw) {
    const [ex, ey] = iso(0, gy, z);
    ctx.save();
    ctx.setTransform(scale, -scale * 0.5, 0, scale, ex * scale, ey * scale);
    draw();
    ctx.restore();
  }

  function hash(text) {
    let value = 0;
    for (const ch of String(text)) value = (value * 31 + ch.charCodeAt(0)) >>> 0;
    return value;
  }

  // -- o escritório ---------------------------------------------------------------------------------
  function create(canvas, options) {
    const opts = options || {};
    const ctx = canvas.getContext('2d');
    const reducedMotion = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const people = new Map();
    let palette = readPalette(canvas);
    let snapshot = { agentes: {}, quadro: {}, movimentos: [], chamado: false };
    let selected = opts.selected || null;
    let scale = 1;
    let frame = null;
    let hover = null;
    const seenMoves = new Set();
    canvas.style.aspectRatio = `${WIDTH} / ${HEIGHT}`;

    for (const member of opts.staff || []) {
      const desk = DESKS[member.id];
      if (!desk) continue;
      const seed = hash(member.id);
      const seat = { x: desk.x + DESK.w / 2, y: desk.y - 0.32 };
      people.set(member.id, {
        ...member,
        desk,
        seat,
        pos: { ...seat },
        path: null,
        standing: false,
        skin: SKIN[seed % SKIN.length],
        hair: HAIR[(seed >> 3) % HAIR.length],
        hairStyle: (seed >> 6) % 3,
        bbox: null,
      });
    }

    function resize() {
      const ratio = global.devicePixelRatio || 1;
      const cssWidth = canvas.clientWidth || WIDTH;
      scale = (cssWidth / WIDTH) * ratio;
      canvas.width = Math.round(WIDTH * scale);
      canvas.height = Math.round(HEIGHT * scale);
      draw();
    }

    function status(id) {
      return (snapshot.agentes[id] || {}).status || 'aguardando';
    }
    function statusColor(value) {
      return {
        trabalhando: palette.ok,
        aprovacao: palette.warn,
        bloqueado: palette.alert,
        desligado: palette.lineStrong,
        aguardando: palette.muted,
      }[value] || palette.muted;
    }

    // -- cenário fixo ---------------------------------------------------------------------------
    function drawFloor() {
      poly(ctx, [iso(0, 0), iso(FLOOR.w, 0), iso(FLOOR.w, FLOOR.d), iso(0, FLOOR.d)], palette.floor);
      for (const zone of ZONES) {
        const corners = [iso(zone.x, zone.y), iso(zone.x + zone.w, zone.y), iso(zone.x + zone.w, zone.y + zone.d), iso(zone.x, zone.y + zone.d)];
        poly(ctx, corners, zone.kind === 'wood' ? palette.wood : palette.floorZone, palette.line);
        if (zone.kind === 'wood') {
          ctx.strokeStyle = palette.woodLine;
          ctx.lineWidth = 1;
          for (let i = 0.6; i < zone.w; i += 0.6) {
            const a = iso(zone.x + i, zone.y);
            const b = iso(zone.x + i, zone.y + zone.d);
            ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
          }
        }
      }
      ctx.strokeStyle = palette.line;
      ctx.lineWidth = 0.6;
      for (let gx = 1; gx < FLOOR.w; gx += 1) {
        const a = iso(gx, 0); const b = iso(gx, FLOOR.d);
        ctx.globalAlpha = 0.35; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      }
      for (let gy = 1; gy < FLOOR.d; gy += 1) {
        const a = iso(0, gy); const b = iso(FLOOR.w, gy);
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      // Nome de cada área, pintado no piso como sinalização.
      for (const zone of ZONES) {
        const [px, py] = iso(zone.x + 0.25, zone.y + zone.d - 0.25);
        ctx.save();
        ctx.translate(px, py);
        ctx.transform(1, -0.5, 1, 0.5, 0, 0);
        ctx.fillStyle = palette.muted;
        ctx.font = '600 9px Jost, "Avenir Next", "Segoe UI", system-ui, sans-serif';
        ctx.globalAlpha = 0.9;
        ctx.fillText(zone.label.toUpperCase(), 0, 0);
        ctx.restore();
      }
    }

    function drawWalls() {
      // parede do fundo e da esquerda, com a borda de cima
      poly(ctx, [iso(0, 0, 0), iso(FLOOR.w, 0, 0), iso(FLOOR.w, 0, WALL), iso(0, 0, WALL)], palette.wall, palette.line);
      poly(ctx, [iso(0, 0, 0), iso(0, FLOOR.d, 0), iso(0, FLOOR.d, WALL), iso(0, 0, WALL)], palette.wallSide, palette.line);
      poly(ctx, [iso(-0.25, -0.25, WALL), iso(FLOOR.w, -0.25, WALL), iso(FLOOR.w, 0, WALL), iso(0, 0, WALL), iso(0, FLOOR.d, WALL), iso(-0.25, FLOOR.d, WALL)], palette.wallTop);
      // ripas verticais no fundo, como no escritório de verdade
      ctx.strokeStyle = palette.line;
      ctx.lineWidth = 1;
      for (let gx = 0.5; gx < FLOOR.w; gx += 0.5) {
        if (gx > 6.6 && gx < 17.2) continue;
        const a = iso(gx, 0, 0.05); const b = iso(gx, 0, WALL - 0.05);
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      }
      drawLogo();
      drawFlag();
      drawBoard();
      drawWordmark();
    }

    function drawLogo() {
      onBackWall(ctx, scale, 7.3, 2.75, () => {
        ctx.strokeStyle = palette.gold;
        ctx.lineJoin = 'round';
        ctx.lineWidth = 3.2;
        ctx.beginPath(); ctx.moveTo(4, 4); ctx.lineTo(24, 44); ctx.lineTo(44, 4); ctx.stroke();
        ctx.lineWidth = 1.6;
        ctx.globalAlpha = 0.65;
        ctx.beginPath(); ctx.moveTo(14, 4); ctx.lineTo(24, 24); ctx.lineTo(34, 4); ctx.stroke();
        // espigas de trigo do símbolo
        ctx.globalAlpha = 0.9;
        for (let i = 0; i < 4; i += 1) {
          ctx.beginPath(); ctx.ellipse(24 - 5, 30 - i * 6, 1.6, 3.4, -0.6, 0, Math.PI * 2); ctx.fillStyle = palette.gold; ctx.fill();
          ctx.beginPath(); ctx.ellipse(24 + 5, 30 - i * 6, 1.6, 3.4, 0.6, 0, Math.PI * 2); ctx.fill();
        }
        ctx.globalAlpha = 1;
      });
    }

    function drawFlag() {
      onBackWall(ctx, scale, 9.6, 2.7, () => {
        ctx.fillStyle = palette.wallTop; ctx.fillRect(-3, -3, 70, 50);
        ctx.fillStyle = FLAG.green; ctx.fillRect(0, 0, 64, 44);
        ctx.fillStyle = FLAG.yellow;
        ctx.beginPath(); ctx.moveTo(32, 4); ctx.lineTo(60, 22); ctx.lineTo(32, 40); ctx.lineTo(4, 22); ctx.closePath(); ctx.fill();
        ctx.fillStyle = FLAG.blue; ctx.beginPath(); ctx.arc(32, 22, 10.5, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = FLAG.white; ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(32, 34, 15, Math.PI * 1.18, Math.PI * 1.82); ctx.stroke();
      });
    }

    function drawBoard() {
      const board = snapshot.quadro || {};
      onBackWall(ctx, scale, 11.6, 2.95, () => {
        const w = 170; const h = 66;
        ctx.fillStyle = palette.chair; roundRect(ctx, -4, -4, w + 8, h + 8, 4); ctx.fill();
        ctx.fillStyle = palette.screen; roundRect(ctx, 0, 0, w, h, 2); ctx.fill();
        ctx.fillStyle = palette.gold;
        ctx.font = '600 8px Jost, "Avenir Next", "Segoe UI", system-ui, sans-serif';
        ctx.fillText('QUADRO DO DIA', 8, 12);
        ctx.fillStyle = palette.screenFg;
        ctx.font = '600 15px "JetBrains Mono", Menlo, Consolas, monospace';
        ctx.fillText(board.receita || 'R$ 0,00', 8, 31);
        ctx.font = '500 7.5px "JetBrains Mono", Menlo, Consolas, monospace';
        ctx.fillStyle = palette.muted;
        ctx.fillText(`pedidos ${board.pedidos || 0}  pagos ${board.pagos || 0}  enviados ${board.enviados || 0}`, 8, 44);
        // barras: pagamentos confirmados por hora
        const bars = board.por_hora || [];
        const max = Math.max(1, ...bars);
        bars.forEach((value, i) => {
          const bh = (value / max) * 12;
          ctx.fillStyle = value ? palette.ok : palette.line;
          ctx.fillRect(8 + i * 11, 61 - Math.max(1, bh), 7, Math.max(1, bh));
        });
        if (board.relogio) {
          ctx.fillStyle = palette.screenFg;
          ctx.font = '500 9px "JetBrains Mono", Menlo, Consolas, monospace';
          ctx.fillText(board.relogio, w - 36, 12);
        }
      });
    }

    function drawWordmark() {
      onLeftWall(ctx, scale, 12.2, 2.4, () => {
        ctx.fillStyle = palette.gold;
        ctx.globalAlpha = 0.85;
        ctx.font = '600 34px "Cormorant Garamond", Georgia, serif';
        ctx.fillText('Veratus', 0, 0);
        ctx.globalAlpha = 1;
      });
    }

    function drawGlassRoom(zone) {
      const e = 0.05;
      ctx.globalAlpha = 1;
      const h = 1.5;
      const front = [iso(zone.x, zone.y + zone.d, 0), iso(zone.x + zone.w, zone.y + zone.d, 0), iso(zone.x + zone.w, zone.y + zone.d, h), iso(zone.x, zone.y + zone.d, h)];
      const side = zone.x > 1
        ? [iso(zone.x, zone.y + e, 0), iso(zone.x, zone.y + zone.d, 0), iso(zone.x, zone.y + zone.d, h), iso(zone.x, zone.y + e, h)]
        : [iso(zone.x + zone.w, zone.y + e, 0), iso(zone.x + zone.w, zone.y + zone.d, 0), iso(zone.x + zone.w, zone.y + zone.d, h), iso(zone.x + zone.w, zone.y + e, h)];
      const alert = zone.id === 'fundador' && snapshot.chamado;
      poly(ctx, side, alert ? 'rgba(224, 131, 110, 0.10)' : palette.glass, alert ? palette.alert : palette.glassEdge);
      poly(ctx, front, alert ? 'rgba(224, 131, 110, 0.10)' : palette.glass, alert ? palette.alert : palette.glassEdge);
    }

    function drawSign(desk) {
      if (!desk.sign) return;
      const base = iso(desk.x + DESK.w + 0.15, desk.y + DESK.d + 0.1, 0);
      const top = iso(desk.x + DESK.w + 0.15, desk.y + DESK.d + 0.1, 1.55);
      ctx.strokeStyle = palette.muted; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(base[0], base[1]); ctx.lineTo(top[0], top[1]); ctx.stroke();
      ctx.font = '600 7.5px Jost, "Avenir Next", "Segoe UI", system-ui, sans-serif';
      const width = ctx.measureText(desk.sign).width + 10;
      ctx.save();
      ctx.translate(top[0], top[1] - 2);
      ctx.transform(1, 0.5, 0, 1, 0, 0);
      ctx.fillStyle = palette.chair; roundRect(ctx, -width / 2, -12, width, 13, 2); ctx.fill();
      ctx.fillStyle = palette.gold; ctx.fillText(desk.sign, -width / 2 + 5, -3);
      ctx.restore();
    }

    function drawPlant(x, y) {
      box(ctx, x, y, 0.45, 0.45, 0, 0.4, { top: palette.pot, left: palette.pot, right: palette.pot });
      const [px, py] = iso(x + 0.22, y + 0.22, 0.75);
      ctx.fillStyle = palette.plant;
      for (let i = 0; i < 5; i += 1) {
        ctx.beginPath();
        ctx.ellipse(px + Math.cos(i * 1.3) * 5, py - 4 + Math.sin(i * 1.7) * 3, 4.5, 8, i * 0.7, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // -- pessoas ---------------------------------------------------------------------------------
    function drawPerson(person, time) {
      const [px, py] = iso(person.pos.x, person.pos.y, 0);
      const st = status(person.id);
      const working = st === 'trabalhando' && !person.standing;
      const bob = working && !reducedMotion ? Math.sin(time / 180 + hash(person.id)) * 0.8 : 0;
      const off = st === 'desligado';
      ctx.save();
      if (off) ctx.globalAlpha = 0.45;
      if (selected === person.id) {
        ctx.strokeStyle = palette.gold; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.ellipse(px, py, 17, 8.5, 0, 0, Math.PI * 2); ctx.stroke();
      }
      let hip;
      if (person.standing) {
        const swing = person.path && !reducedMotion ? Math.sin(time / 90) * 3 : 0;
        ctx.strokeStyle = palette.pants; ctx.lineWidth = 4; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(px - 3, py - 20); ctx.lineTo(px - 3 + swing, py - 2); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(px + 3, py - 20); ctx.lineTo(px + 3 - swing, py - 2); ctx.stroke();
        hip = py - 20;
      } else {
        // cadeira atrás da pessoa
        ctx.fillStyle = palette.chair;
        roundRect(ctx, px - 10, py - 40, 20, 26, 5); ctx.fill();
        ctx.fillRect(px - 1.5, py - 14, 3, 10);
        ctx.beginPath(); ctx.ellipse(px, py - 3, 9, 3.5, 0, 0, Math.PI * 2); ctx.fill();
        hip = py - 16;
      }
      const top = hip - 22 + bob;
      ctx.fillStyle = palette.shirt;
      ctx.strokeStyle = palette.shirtEdge; ctx.lineWidth = 1;
      roundRect(ctx, px - 8, top, 16, hip - top + 2, 5); ctx.fill(); ctx.stroke();
      // gravata dourada no Harvey; colarinho nos outros
      if (person.id === 'sales-agent') {
        ctx.fillStyle = palette.gold;
        ctx.beginPath(); ctx.moveTo(px, top + 2); ctx.lineTo(px + 2.4, top + 10); ctx.lineTo(px, top + 14); ctx.lineTo(px - 2.4, top + 10); ctx.closePath(); ctx.fill();
      } else {
        ctx.strokeStyle = palette.shirtEdge;
        ctx.beginPath(); ctx.moveTo(px - 4, top + 1); ctx.lineTo(px, top + 5); ctx.lineTo(px + 4, top + 1); ctx.stroke();
      }
      // braços: sobre a mesa quando trabalha
      ctx.strokeStyle = palette.shirt; ctx.lineWidth = 3.6; ctx.lineCap = 'round';
      const reach = working ? 7 + bob : 3;
      ctx.beginPath(); ctx.moveTo(px - 7, top + 6); ctx.lineTo(px - 9, top + 6 + reach); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(px + 7, top + 6); ctx.lineTo(px + 9, top + 6 + reach); ctx.stroke();
      // cabeça
      const hy = top - 8;
      ctx.fillStyle = person.skin;
      ctx.beginPath(); ctx.arc(px, hy, 7, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = person.hair;
      ctx.beginPath();
      if (person.hairStyle === 0) ctx.arc(px, hy - 1.5, 7.2, Math.PI * 1.05, Math.PI * 1.95);
      else if (person.hairStyle === 1) { ctx.arc(px, hy - 1, 7.4, Math.PI * 0.95, Math.PI * 2.05); ctx.lineTo(px + 7, hy + 4); ctx.lineTo(px - 7, hy + 4); }
      else ctx.ellipse(px, hy - 4, 7.4, 4.2, 0, Math.PI, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      person.bbox = { x: px - 14, y: hy - 10, w: 28, h: py - hy + 12 };
      person.head = [px, hy];
    }

    function drawDesk(person) {
      const desk = person.desk;
      const colors = { top: palette.deskTop, left: palette.deskLeft, right: palette.deskRight };
      box(ctx, desk.x, desk.y, DESK.w, DESK.d, 0, DESK.h, colors);
      const st = status(person.id);
      const light = statusColor(st);
      const count = desk.monitors || 1;
      const offsets = count === 1 ? [0.18] : count === 2 ? [0.08, 1.32] : [0.0, 0.7, 1.4];
      for (const o of offsets) {
        const mx = desk.x + o;
        const my = desk.y + DESK.d - 0.32;
        box(ctx, mx + 0.22, my + 0.08, 0.08, 0.06, DESK.h, 0.18, { top: palette.monitor, left: palette.monitor, right: palette.monitor });
        box(ctx, mx, my, 0.52, 0.07, DESK.h + 0.18, 0.4, { top: palette.monitor, left: palette.monitor, right: palette.monitor });
        // luz de status no alto do monitor
        const a = iso(mx + 0.06, my + 0.07, DESK.h + 0.56);
        const b = iso(mx + 0.46, my + 0.07, DESK.h + 0.56);
        ctx.strokeStyle = light; ctx.lineWidth = st === 'desligado' ? 1 : 2.4;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      }
      if (person.id === 'fundador' && snapshot.chamado) {
        const [bx, by] = iso(desk.x + 1.6, desk.y + 0.3, DESK.h + 0.05);
        ctx.fillStyle = palette.alert;
        ctx.beginPath(); ctx.arc(bx, by - 5, 5, Math.PI, 0); ctx.lineTo(bx + 6, by); ctx.lineTo(bx - 6, by); ctx.closePath(); ctx.fill();
      }
    }

    function drawTag(person) {
      const info = snapshot.agentes[person.id] || {};
      const tag = info.tag;
      const show = tag && (tag.texto || '').length;
      const isSelected = selected === person.id;
      const isHover = hover === person.id;
      if (!person.head) return;
      const [hx, hy] = person.head;
      if (isSelected || isHover) {
        ctx.font = '600 10px Jost, "Avenir Next", "Segoe UI", system-ui, sans-serif';
        const label = person.nome;
        const width = ctx.measureText(label).width + 12;
        ctx.fillStyle = palette.gold;
        roundRect(ctx, hx - width / 2, hy - (show ? 44 : 30), width, 15, 7); ctx.fill();
        ctx.fillStyle = palette.onGold;
        ctx.fillText(label, hx - width / 2 + 6, hy - (show ? 33 : 19));
      }
      if (!show) return;
      const tone = { ganho: palette.ok, alerta: palette.alert, aviso: palette.warn }[tag.tom] || palette.lineStrong;
      ctx.font = '500 9.5px "JetBrains Mono", Menlo, Consolas, monospace';
      const text = tag.texto.length > 26 ? `${tag.texto.slice(0, 25)}…` : tag.texto;
      const width = ctx.measureText(text).width + 10;
      const x = hx - width / 2;
      const y = hy - 27;
      ctx.fillStyle = palette.bg;
      roundRect(ctx, x, y, width, 14, 3); ctx.fill();
      ctx.strokeStyle = tone; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.fillStyle = tag.tom === 'ganho' ? palette.ok : tag.tom === 'alerta' ? palette.alert : tag.tom === 'aviso' ? palette.warn : palette.fg;
      ctx.fillText(text, x + 5, y + 10);
    }

    function drawCopa() {
      // mesa de reunião, balcão do café, sofá e plantas
      box(ctx, 15.8, 13.2, 3.0, 1.2, 0, 0.68, { top: palette.deskTop, left: palette.deskLeft, right: palette.deskRight });
      for (const [sx, sy] of MEETING_SEATS) {
        const [cx, cy] = iso(sx, sy, 0);
        ctx.fillStyle = palette.chair;
        ctx.beginPath(); ctx.ellipse(cx, cy - 8, 7, 3.5, 0, 0, Math.PI * 2); ctx.fill();
      }
      box(ctx, 21.2, 12.0, 2.2, 0.8, 0, 0.9, { top: palette.deskTop, left: palette.deskLeft, right: palette.deskRight });
      box(ctx, 21.5, 12.15, 0.45, 0.4, 0.9, 0.42, { top: palette.monitor, left: palette.monitor, right: palette.monitor });
      box(ctx, 20.6, 14.6, 2.6, 0.9, 0, 0.42, { top: palette.chair, left: palette.chair, right: palette.chair });
      box(ctx, 20.6, 15.3, 2.6, 0.25, 0.42, 0.5, { top: palette.chair, left: palette.chair, right: palette.chair });
      drawPlant(13.9, 15.9);
      drawPlant(23.0, 11.9);
      drawPlant(0.7, 10.9);
      drawPlant(16.4, 0.7);
      // expedição: prateleira com caixas e a bancada de embalar
      box(ctx, 22.6, 6.2, 0.8, 4.2, 0, 1.5, { top: palette.monitor, left: palette.chair, right: palette.monitor });
      const shipped = Math.min(12, (snapshot.quadro || {}).enviados || 0);
      for (let i = 0; i < shipped; i += 1) {
        const bx = 22.68; const by = 6.35 + (i % 6) * 0.68; const bz = i < 6 ? 0.25 : 1.05;
        box(ctx, bx, by, 0.6, 0.5, bz, 0.4, { top: palette.wood, left: palette.woodLine, right: palette.wood });
      }
      box(ctx, 20.8, 7.0, 1.2, 1.6, 0, 0.8, { top: palette.wood, left: palette.woodLine, right: palette.woodLine });
      // caixa do Mercado Pago no atendimento
      const pay = (snapshot.agentes['mercado-pago'] || {}).status === 'trabalhando';
      box(ctx, 15.0, 3.6, 0.7, 0.5, 0, 1.0, { top: palette.monitor, left: palette.monitor, right: palette.monitor });
      const [tx, ty] = iso(15.35, 3.85, 1.05);
      ctx.fillStyle = pay ? palette.ok : palette.lineStrong;
      ctx.beginPath(); ctx.arc(tx, ty - 2, 3, 0, Math.PI * 2); ctx.fill();
    }

    // -- movimento ---------------------------------------------------------------------------------
    function aisleFor(y) {
      return y < 5.6 ? AISLE_Y[0] : y < 11.4 ? (y < 8.2 ? AISLE_Y[0] : AISLE_Y[1]) : AISLE_Y[1];
    }
    function route(from, to) {
      const a = aisleFor(from.y);
      const b = aisleFor(to.y);
      const points = [{ x: from.x, y: from.y + 0.7 }, { x: from.x, y: a }];
      if (a !== b) points.push({ x: AISLE_X, y: a }, { x: AISLE_X, y: b });
      points.push({ x: to.x, y: b }, { x: to.x, y: to.y });
      return points;
    }
    function walk(person, target, dwell) {
      if (reducedMotion) return;
      const go = route(person.seat, target);
      const back = route(target, person.seat).slice(1);
      person.path = { points: [{ ...person.seat }, ...go, ...back.reverse().reverse(), { ...person.seat }], index: 0, t: 0, dwellAt: go.length, dwell: dwell || 900, waited: 0 };
      person.standing = true;
      ensureLoop();
    }
    function advance(person, dt) {
      const path = person.path;
      if (!path) return false;
      if (path.index === path.dwellAt && path.waited < path.dwell) {
        path.waited += dt;
        return true;
      }
      const a = path.points[path.index];
      const b = path.points[path.index + 1];
      if (!b) {
        person.path = null; person.standing = false; person.pos = { ...person.seat };
        return false;
      }
      const length = Math.hypot(b.x - a.x, b.y - a.y) || 0.01;
      path.t += (dt / 1000) * 3.2 / length;
      if (path.t >= 1) { path.index += 1; path.t = 0; person.pos = { ...b }; return true; }
      person.pos = { x: a.x + (b.x - a.x) * path.t, y: a.y + (b.y - a.y) * path.t };
      return true;
    }

    function applyMoves(moves, animate) {
      for (const move of moves || []) {
        if (!move || seenMoves.has(move.id)) continue;
        seenMoves.add(move.id);
        if (!animate) continue;
        const person = people.get(move.quem);
        if (!person || person.path) continue;
        const target = move.para === 'reuniao'
          ? { x: MEETING_SEATS[hash(person.id) % MEETING_SEATS.length][0], y: MEETING_SEATS[hash(person.id) % MEETING_SEATS.length][1] }
          : SPOTS[move.para] || (people.get(move.para) || {}).seat;
        if (target) walk(person, { x: target.x, y: target.y + (people.get(move.para) ? 0.9 : 0) }, move.para === 'reuniao' ? 2400 : 1100);
      }
    }

    // -- quadro ---------------------------------------------------------------------------------------
    let last = 0;
    function tick(time) {
      frame = null;
      const dt = last ? Math.min(64, time - last) : 16;
      last = time;
      let moving = false;
      for (const person of people.values()) moving = advance(person, dt) || moving;
      draw(time);
      const busy = !reducedMotion && [...people.keys()].some((id) => status(id) === 'trabalhando');
      if (moving || busy) ensureLoop(); else last = 0;
    }
    function ensureLoop() {
      if (frame === null) frame = global.requestAnimationFrame(tick);
    }

    function draw(time) {
      const now = time || 0;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = palette.bg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      drawFloor();
      drawWalls();
      for (const zone of ZONES) if (zone.kind === 'glass' && zone.id === 'diretoria') drawGlassRoom(zone);
      drawCopa();
      // pintor: o que está mais ao fundo primeiro
      const items = [];
      for (const person of people.values()) {
        items.push({ depth: person.desk.x + person.desk.y + DESK.d, kind: 'desk', person });
        items.push({ depth: person.pos.x + person.pos.y + (person.standing ? 0.9 : 0), kind: 'person', person });
      }
      items.sort((a, b) => a.depth - b.depth);
      for (const item of items) {
        if (item.kind === 'desk') { drawDesk(item.person); drawSign(item.person.desk); }
        else drawPerson(item.person, now);
      }
      for (const zone of ZONES) if (zone.kind === 'glass' && zone.id === 'fundador') drawGlassRoom(zone);
      for (const person of people.values()) drawTag(person);
    }

    // -- interação --------------------------------------------------------------------------------------
    function pick(event) {
      const rect = canvas.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * WIDTH;
      const y = ((event.clientY - rect.top) / rect.height) * HEIGHT;
      let best = null;
      for (const person of people.values()) {
        const b = person.bbox;
        if (b && x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) {
          if (!best || person.pos.x + person.pos.y > best.pos.x + best.pos.y) best = person;
        }
      }
      return best;
    }
    canvas.addEventListener('click', (event) => {
      const person = pick(event);
      if (person) { selected = person.id; draw(); if (opts.onSelect) opts.onSelect(person.id); }
    });
    canvas.addEventListener('mousemove', (event) => {
      const person = pick(event);
      const id = person ? person.id : null;
      canvas.style.cursor = id ? 'pointer' : 'default';
      if (id !== hover) { hover = id; draw(); }
    });
    canvas.addEventListener('mouseleave', () => { if (hover) { hover = null; draw(); } });

    const refreshPalette = () => { palette = readPalette(canvas); draw(); };
    if (global.matchMedia) {
      const media = global.matchMedia('(prefers-color-scheme: dark)');
      if (media.addEventListener) media.addEventListener('change', refreshPalette);
    }
    if (global.MutationObserver) {
      new MutationObserver(refreshPalette).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    }
    if (global.ResizeObserver) new ResizeObserver(resize).observe(canvas);
    else global.addEventListener('resize', resize);
    resize();

    return {
      width: WIDTH,
      height: HEIGHT,
      update(next, flags) {
        snapshot = { agentes: {}, quadro: {}, movimentos: [], chamado: false, ...(next || {}) };
        applyMoves(snapshot.movimentos, Boolean(flags && flags.animar));
        draw();
        ensureLoop();
      },
      select(id) {
        selected = id;
        draw();
      },
      focusPoint(id) {
        const person = people.get(id);
        if (!person) return null;
        const [px] = iso(person.seat.x, person.seat.y, 0);
        return px / WIDTH;
      },
    };
  }

  global.VeratusOffice = { create, DESKS, ZONES };
})(window);
