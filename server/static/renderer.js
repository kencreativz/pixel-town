import { drawSprite, resolveSprite, shade } from "/static/sprites.js";

const SPEED = 48;
const LINE = 22;

function wrap(text) {
  const lines = [];
  let cur = "";
  for (let word of String(text).split(/\s+/).filter(Boolean)) {
    while (word.length > LINE) {
      if (cur) {
        lines.push(cur);
        cur = "";
      }
      lines.push(word.slice(0, LINE));
      word = word.slice(LINE);
    }
    if (!cur) cur = word;
    else if (cur.length + 1 + word.length <= LINE) cur += ` ${word}`;
    else {
      lines.push(cur);
      cur = word;
    }
  }
  if (cur) lines.push(cur);
  if (lines.length <= 3) return lines;
  const out = lines.slice(0, 3);
  out[2] = `${out[2].slice(0, LINE - 1)}…`;
  return out;
}

export function createRenderer(def, canvas, overlay) {
  const ts = def.map.tile_size;
  const W = def.map.width * ts;
  const H = def.map.height * ts;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  let slot = null;

  for (const loc of Object.values(def.locations)) {
    const el = document.createElement("div");
    el.className = "loc-label";
    el.textContent = loc.name;
    el.style.left = `${((loc.rect.x * ts) / W) * 100}%`;
    el.style.top = `${((loc.rect.y * ts) / H) * 100}%`;
    overlay.appendChild(el);
  }

  const chars = new Map();
  for (const c of def.characters) {
    const label = document.createElement("div");
    label.className = "name-label";
    const marker = document.createElement("span");
    if (c.kind === "scripted" || c.kind === "hybrid") marker.className = `marker ${c.kind}`;
    const nm = document.createElement("span");
    nm.textContent = c.name;
    label.append(marker, nm);
    label.hidden = true;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.hidden = true;
    overlay.append(label, bubble);
    chars.set(c.id, {
      look: resolveSprite(c.sprite),
      x: 0,
      y: 0,
      tx: 0,
      ty: 0,
      placed: false,
      visible: false,
      moving: false,
      label,
      bubble,
      bubbleTimer: null,
    });
  }

  function setState(state) {
    slot = state.clock.slot;
    const groups = {};
    for (const [cid, cs] of Object.entries(state.characters)) {
      if (!chars.has(cid)) continue;
      (groups[cs.location] ||= []).push(cid);
    }
    for (const ch of chars.values()) ch.visible = false;
    for (const [locId, ids] of Object.entries(groups)) {
      const loc = def.locations[locId];
      if (!loc) continue;
      ids.sort((a, b) => def.turn_order.indexOf(a) - def.turn_order.indexOf(b));
      const { x, y, width, height } = loc.rect;
      ids.forEach((cid, i) => {
        const ch = chars.get(cid);
        const cx = x + (i % width);
        const cy = y + (Math.floor(i / width) % height);
        ch.tx = cx * ts + (ts - 12) / 2;
        ch.ty = cy * ts + ts - 16;
        ch.visible = true;
        if (!ch.placed) {
          ch.x = ch.tx;
          ch.y = ch.ty;
          ch.placed = true;
        }
      });
    }
  }

  function showBubble(cid, text) {
    const ch = chars.get(cid);
    if (!ch) return;
    ch.bubble.textContent = wrap(text).join("\n");
    ch.bubble.hidden = false;
    clearTimeout(ch.bubbleTimer);
    ch.bubbleTimer = setTimeout(() => {
      ch.bubble.hidden = true;
    }, 6000);
  }

  function rect(color, x, y, w, h) {
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, h);
  }

  function drawLocation(loc) {
    const { x, y, width, height } = loc.rect;
    const light = shade(loc.color, 8);
    for (let i = 0; i < width; i++) {
      for (let j = 0; j < height; j++) {
        rect((i + j) % 2 ? light : loc.color, (x + i) * ts, (y + j) * ts, ts, ts);
      }
    }
    const px = x * ts;
    const py = y * ts;
    const pw = width * ts;
    const ph = height * ts;
    const border = shade(loc.color, -30);
    rect(border, px, py, pw, 1);
    rect(border, px, py + ph - 1, pw, 1);
    rect(border, px, py, 1, ph);
    rect(border, px + pw - 1, py, 1, ph);

    switch (loc.type) {
      case "office":
        rect("#8b5a2b", px + 4, py + 6, ts * 2 - 8, ts - 12);
        rect("#222222", px + 8, py + 2, 6, 5);
        rect("#555555", px + 10, py + 7, 2, 2);
        break;
      case "workshop":
        for (let row = 0; row < height; row++) {
          if (row % 2 === 0) rect("#7a5a3a", px + 2, py + row * ts + ts / 4, pw - 4, ts / 2);
        }
        break;
      case "tavern":
        rect("#5a3a1a", px, py, pw, 10);
        rect("#8b5a2b", px + 6, py + ph - 14, 8, 6);
        rect("#8b5a2b", px + pw - 14, py + ph - 14, 8, 6);
        break;
      case "home":
        rect("#c0504d", px + 4, py + 8, ts * 2 - 8, ts - 16);
        rect("#ffffff", px + 4, py + 11, 5, 10);
        break;
      case "plaza": {
        const cx = px + pw / 2;
        const cy = py + ph / 2;
        rect("#9a9a9a", cx - 12, cy - 12, 24, 24);
        rect("#3a7bd5", cx - 7, cy - 7, 14, 14);
        break;
      }
      default:
        break;
    }
  }

  let last = performance.now();
  function frame(now) {
    const dt = (now - last) / 1000;
    last = now;
    rect("#2e3a2e", 0, 0, W, H);
    for (let x = 0; x < def.map.width; x++) {
      for (let y = 0; y < def.map.height; y++) {
        if ((x + y) % 2 === 1) rect("#334433", x * ts, y * ts, ts, ts);
      }
    }
    for (const loc of Object.values(def.locations)) drawLocation(loc);

    const snap = reduced.matches;
    const vis = [];
    for (const ch of chars.values()) {
      if (!ch.visible) {
        ch.label.hidden = true;
        ch.bubble.hidden = true;
        ch.moving = false;
        continue;
      }
      const dx = ch.tx - ch.x;
      const dy = ch.ty - ch.y;
      const dist = Math.hypot(dx, dy);
      if (dist > 0) {
        if (snap) {
          ch.x = ch.tx;
          ch.y = ch.ty;
        } else {
          const step = Math.min(dist, SPEED * dt);
          ch.x += (dx / dist) * step;
          ch.y += (dy / dist) * step;
        }
      }
      ch.moving = ch.x !== ch.tx || ch.y !== ch.ty;
      vis.push(ch);
    }
    vis.sort((a, b) => a.y - b.y);
    for (const ch of vis) {
      drawSprite(ctx, ch.look, Math.round(ch.x), Math.round(ch.y), ch.moving ? Math.floor(now / 150) % 2 : 0);
      ch.label.hidden = false;
      ch.label.style.left = `${((ch.x + 6) / W) * 100}%`;
      ch.label.style.top = `${((ch.y + 16) / H) * 100}%`;
      ch.bubble.style.left = `${((ch.x + 6) / W) * 100}%`;
      ch.bubble.style.top = `${(ch.y / H) * 100}%`;
    }
    if (slot === "Afternoon") rect("rgba(255,160,0,0.08)", 0, 0, W, H);
    else if (slot === "Night") rect("rgba(10,10,60,0.35)", 0, 0, W, H);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setState, showBubble };
}
