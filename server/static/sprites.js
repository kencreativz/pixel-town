const PRESETS = {
  security_guard: { outfit: "#2b3a67", accessory: "cap" },
  engineer_blue: { outfit: "#2f6fdb", accessory: "hardhat" },
  laborer_yellow: { outfit: "#e0b400", accessory: "none" },
  shopkeeper: { outfit: "#8e44ad", accessory: "apron" },
  landlord: { outfit: "#7a3b3b", accessory: "badge" },
  teller: { outfit: "#2e7d5b", accessory: "badge" },
  programmer: { outfit: "#4a5a7a", accessory: "glasses" },
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

const PANTS = "#3a3a4a";
const PANTS_DARK = "#2a2a36";
const SHOES = "#2a1a10";
const EYE = "#111111";
const CHEEK = "#e8a090";
const tones = (hex) => ({ base: hex, light: shade(hex, 18), dark: shade(hex, -22) });
const MIRRORED = { left: "right", "front-left": "front-right", "back-left": "back-right" };

// Draws a 16×24 character with its top-left at (x, y). Options:
// - `dir`: "front", "back", "left", "right" or a diagonal ("front-left", "front-right", "back-left", "back-right");
//   the left-hand views are the right-hand ones mirrored.
// - `pose`: "stand" (breathes), "walk" (4 frames: legs step, arms swing, body bobs), "sit" (3 px lower, legs bent;
//   facing back it is working, so the hands move) or "sleep" (24×8 lying under a blanket, "z"s drift up).
// - `item`: "cup", "drink", "food" or "smoke", held in the right hand and raised to the mouth for 0.8 s every 4 s
//   of `t` (seconds); smoke puffs follow. Not drawn when facing away.
// - `solid`: fill every part with this one colour (used to draw the outline).
export function drawSprite(ctx, look, x, y, o = {}) {
  const { frame = 0, solid, dir = "front", pose = "stand", item = null, t = 0 } = o;
  const hair = tones(look.hair);
  const skin = tones(look.skin);
  const cloth = tones(look.outfit);

  if (pose === "sleep") {
    const s = (color, rx, ry, w, h) => {
      ctx.fillStyle = solid || color;
      ctx.fillRect(x + rx, y + ry, w, h);
    };
    s(hair.base, 0, 0, 3, 8);
    s(hair.light, 1, 1, 1, 3);
    s(skin.base, 3, 1, 5, 6);
    s(skin.dark, 3, 6, 5, 1);
    s(skin.dark, 5, 3, 2, 1); // closed eyes
    s(cloth.base, 8, 0, 16, 8);
    s(cloth.light, 8, 0, 16, 1);
    s(cloth.light, 8, 0, 2, 8); // blanket fold
    s(cloth.dark, 8, 7, 16, 1);
    if (!solid) {
      const k = Math.floor(t * 2) % 3;
      s("#ffffff", 9 + k, -3 - k, 3, 1);
      s("#ffffff", 10 + k, -4 - k, 1, 1);
      s("#ffffff", 9 + k, -5 - k, 3, 1);
    }
    return;
  }

  const mirror = dir in MIRRORED;
  const view = MIRRORED[dir] || dir; // "front", "back", "right", "front-right" or "back-right"
  const away = view === "back" || view === "back-right";
  const sit = pose === "sit";
  const walk = pose === "walk";
  const pass = walk && frame % 2 === 1;
  const breathe = pose === "stand" && Math.floor(t / 0.7) % 2 === 1;
  const drop = sit ? 3 : 0;
  const bob = pass || breathe ? -1 : 0; // the upper body rises on pass frames and when breathing in
  const r = (color, rx, ry, w, h) => {
    ctx.fillStyle = solid || color;
    ctx.fillRect(x + (mirror ? 16 - rx - w : rx), y + ry + drop, w, h);
  };
  const u = (color, rx, ry, w, h) => r(color, rx, ry + bob, w, h);
  const cycle = t % 4;
  const raised = Boolean(item) && !away && !walk && cycle < 0.8;
  const cap = shade(look.outfit, -25);
  const brim = shade(look.outfit, -40);

  function drawItem(ix, iy) {
    if (item === "cup") {
      u("#f2f2f2", ix, iy, 3, 4);
      u("#6b4423", ix, iy, 3, 1);
      u("#d8d8d8", ix, iy + 3, 3, 1);
      u("#f2f2f2", ix + 3, iy + 1, 1, 2);
    } else if (item === "drink") {
      u("#f2f2f2", ix, iy, 3, 1);
      u("#d98c1a", ix, iy + 1, 3, 3);
      u("#b8700f", ix + 2, iy + 1, 1, 3);
      u("#d8d8d8", ix + 3, iy + 1, 1, 2);
    } else if (item === "food") {
      u("#d9a066", ix, iy, 4, 1);
      u("#5aa04a", ix, iy + 1, 4, 1);
      u("#a0522d", ix, iy + 2, 4, 1);
      u("#d9a066", ix, iy + 3, 4, 1);
    } else if (item === "smoke") {
      u("#f2f2f2", ix, iy + 1, 3, 1);
      u("#ff7a1a", ix + 3, iy + 1, 1, 1);
      if (!solid && cycle >= 0.8 && cycle < 2.2) {
        const k = Math.floor((cycle - 0.8) * 5);
        u("#c8c8c8", 8, 2 - k, 2, 1);
        u("#dddddd", 9, -k, 1, 1);
      }
    }
  }

  // ---- legs (no bob) ----
  if (view === "right") {
    r(PANTS, 5, 17, 6, 2);
    if (sit) {
      r(PANTS, 6, 17, 6, 2); // thigh forward
      r(SHOES, 10, 19, 3, 2);
    } else if (walk && !pass) {
      r(PANTS_DARK, 5, 19, 2, 3); // back leg
      r(SHOES, 4, 22, 3, 2);
      r(PANTS, 9, 19, 2, 3); // front leg
      r(SHOES, 9, 22, 3, 2);
    } else {
      r(PANTS_DARK, 6, 19, 1, 3);
      r(PANTS, 7, 19, 2, 3);
      r(SHOES, 7, 22, 3, 2);
    }
  } else {
    const s = view === "front-right" || view === "back-right" ? 1 : 0;
    r(PANTS, 4 + s, 17, 8, 2);
    if (sit) {
      if (!away) {
        r(SHOES, 5 + s, 19, 2, 2);
        r(SHOES, 9 + s, 19, 2, 2);
      }
    } else {
      for (const [side, lx] of [
        [0, 5 + s],
        [1, 9 + s],
      ]) {
        const up = walk && ((frame === 0 && side === 1) || (frame === 2 && side === 0)) ? 1 : 0;
        r(PANTS, lx, 19, 2, 3 - up);
        r(PANTS_DARK, lx + 1, 19, 1, 3 - up);
        r(SHOES, lx - (side === 0 ? 1 : 0), 22 - up, 3, 2);
      }
    }
  }

  // ---- body, head, arms ----
  const arm = (ax, dy) => {
    u(cloth.dark, ax, 11 + dy, 2, 4);
    u(skin.base, ax, 15 + dy, 2, 2);
  };
  const swing = walk ? (frame === 0 ? 1 : frame === 2 ? -1 : 0) : 0;

  if (view === "right") {
    u(cloth.base, 5, 11, 6, 6);
    u(cloth.dark, 5, 11, 1, 6);
    u(cloth.light, 10, 11, 1, 6);
    u(cloth.dark, 5, 16, 6, 1);
    u(skin.dark, 7, 10, 2, 1); // neck
    u(skin.base, 4, 2, 9, 8);
    u(skin.dark, 5, 9, 7, 1);
    u(hair.base, 4, 0, 9, 3);
    u(hair.light, 6, 0, 4, 1);
    u(hair.base, 4, 3, 3, 5);
    u(hair.dark, 4, 7, 3, 1);
    u(skin.dark, 7, 5, 1, 2); // ear
    u(EYE, 10, 5, 1, 2);
    u(skin.base, 13, 6, 1, 1); // nose
    u(CHEEK, 10, 7, 1, 1);
    u(skin.dark, 11, 8, 1, 1); // mouth
    if (raised) {
      u(cloth.dark, 8, 9, 2, 3);
      u(skin.base, 10, 8, 2, 2);
    } else arm(walk ? 7 + 2 * swing : 7, 0);
    if (item) drawItem(raised ? 11 : 10, raised ? 6 : 14);
  } else {
    const s = view === "front-right" || view === "back-right" ? 1 : 0;
    u(cloth.base, 4, 11, 8, 6);
    u(cloth.light, 4, 11, 1, 6);
    u(cloth.dark, 11, 11, 1, 6);
    u(cloth.dark, 4, 16, 8, 1);
    u(skin.dark, 7, 10, 2, 1); // neck
    if (away) {
      u(hair.base, 3, 0, 10, 9);
      u(hair.light, 5, 1, 4, 1);
      u(hair.dark, 3, 8, 10, 1);
      if (s) u(skin.base, 12, 4, 1, 4); // cheek seen past the hair
    } else {
      u(cloth.dark, 6, 11, 4, 1); // collar
      u(skin.base, 3, 2, 10, 8);
      u(skin.dark, 4, 9, 8, 1);
      u(hair.base, 3, 0, 10, 3);
      u(hair.light, 5, 0, 4, 1);
      u(hair.base, 3, 3, 1 + s, 4 + s);
      u(hair.base, 12, 3, 1, 3);
      u(hair.base, 4 + s, 3, 3, 1); // bangs
      u(hair.base, 9 + s, 3, 2, 1);
      u(EYE, 5 + s, 5, 1, 2);
      u(EYE, 10 + s, 5, 1, 2);
      u(CHEEK, 4 + s, 7, 1, 1);
      u(CHEEK, 11 + s, 7, 1, 1);
      u(skin.dark, 7 + s, 8, 2, 1); // mouth
    }
    if (sit && away) {
      // working at the desk/bench: hands move up and down in turn
      const k = Math.floor(t * 4) % 2;
      arm(2, k ? -1 : 0);
      arm(12, k ? 0 : -1);
    } else {
      arm(2, swing);
      if (raised) {
        u(cloth.dark, 12, 10, 2, 2);
        u(skin.base, 10, 8, 2, 2);
      } else arm(12, -swing);
    }
    if (item && !away) drawItem(raised ? 8 : 13, raised ? 6 : 14);
  }

  // ---- accessories ----
  const side = view === "right";
  const quarter = view === "front-right";
  switch (look.accessory) {
    case "cap":
      u(cap, side ? 4 : 3, 0, side ? 9 : 10, 3);
      if (side) u(brim, 10, 3, 5, 1);
      else if (quarter) u(brim, 5, 3, 9, 1);
      else if (!away) u(brim, 3, 3, 10, 1);
      break;
    case "hardhat":
      u("#f5c518", 2, 0, 12, 3);
      u("#ffe680", 4, 0, 4, 1);
      u("#d4a800", side ? 3 : 1, 3, side ? 12 : 14, 1);
      break;
    case "apron":
      if (side) u("#f2f2f2", 9, 12, 2, 5);
      else if (away) u("#f2f2f2", 6, 14, 4, 1);
      else {
        u("#f2f2f2", 5 + (quarter ? 1 : 0), 12, 6, 5);
        u("#f2f2f2", 5, 11, 1, 1);
        u("#f2f2f2", 10, 11, 1, 1);
      }
      break;
    case "badge":
      if (side) u("#ffd700", 9, 12, 1, 2);
      else if (!away) u("#ffd700", 10, 12, 2, 2);
      break;
    case "glasses": {
      // dark frames around the eyes (front and 3/4: two lenses and a bridge; side: one lens and the arm to the ear)
      const g = "#222222";
      if (side) {
        u(g, 9, 4, 3, 1);
        u(g, 9, 5, 1, 2);
        u(g, 11, 5, 1, 2);
        u(g, 7, 5, 2, 1);
      } else if (!away) {
        const s = quarter ? 1 : 0;
        for (const lx of [4 + s, 9 + s]) {
          u(g, lx, 4, 3, 1);
          u(g, lx, 5, 1, 2);
          u(g, lx + 2, 5, 1, 2);
        }
        u(g, 7 + s, 5, 2, 1);
      }
      break;
    }
    default:
      break;
  }
}
