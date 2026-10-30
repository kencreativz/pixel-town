const PRESETS = {
  security_guard: { outfit: "#2b3a67", accessory: "cap" },
  engineer_blue: { outfit: "#2f6fdb", accessory: "hardhat" },
  laborer_yellow: { outfit: "#e0b400", accessory: "none" },
  shopkeeper: { outfit: "#8e44ad", accessory: "apron" },
  landlord: { outfit: "#7a3b3b", accessory: "badge" },
};

export function shade(hex, pct) {
  const n = parseInt(hex.slice(1, 7), 16);
  const target = pct >= 0 ? 255 : 0;
  const f = Math.abs(pct) / 100;
  const ch = (v) => Math.round(v + (target - v) * f);
  const r = ch((n >> 16) & 255);
  const g = ch((n >> 8) & 255);
  const b = ch(n & 255);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

export function resolveSprite(sprite) {
  const s = sprite || {};
  const preset = PRESETS[s.style] || { outfit: "#888888", accessory: "none" };
  return {
    skin: s.skin || "#f1c27d",
    hair: s.hair || "#3b2f2f",
    outfit: s.outfit || preset.outfit,
    accessory: s.accessory || preset.accessory,
  };
}

export function drawSprite(ctx, look, x, y, frame) {
  const r = (color, rx, ry, w, h) => {
    ctx.fillStyle = color;
    ctx.fillRect(x + rx, y + ry, w, h);
  };
  r(look.hair, 3, 0, 6, 2);
  r(look.skin, 3, 2, 6, 5);
  r("#111111", 4, 4, 1, 1);
  r("#111111", 7, 4, 1, 1);
  r(look.outfit, 2, 7, 8, 5);
  r(look.skin, 1, 7, 1, 4);
  r(look.skin, 10, 7, 1, 4);
  if (frame === 1) {
    r("#2b2b2b", 3, 12, 2, 3);
    r("#2b2b2b", 7, 12, 2, 4);
  } else {
    r("#2b2b2b", 3, 12, 2, 4);
    r("#2b2b2b", 7, 12, 2, 3);
  }
  switch (look.accessory) {
    case "cap": {
      const c = shade(look.outfit, -25);
      r(c, 2, 0, 8, 2);
      r(c, 2, 2, 8, 1);
      break;
    }
    case "hardhat":
      r("#f5c518", 2, 0, 8, 3);
      break;
    case "apron":
      r("#f2f2f2", 3, 9, 6, 3);
      break;
    case "badge":
      r("#ffd700", 7, 8, 2, 2);
      break;
    default:
      break;
  }
}
