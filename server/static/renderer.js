import { drawSprite, resolveSprite, shade } from "/static/sprites.js";

const SPEED = 32;
const WALK_FRAME_MS = 150; // 4 walk frames
const SHADOW = "rgba(0,0,0,0.28)";
// Mix a vertical and a horizontal facing into a diagonal ("front" + "left" -> "front-left").
function mixDir(a, b) {
  const v = [a, b].find((d) => d === "front" || d === "back");
  const h = [a, b].find((d) => d === "left" || d === "right");
  return v && h ? `${v}-${h}` : b;
}
// What an idle character may hold, by location type (picked per character and slot).
const ITEMS = {
  office: ["cup", "food", null],
  workshop: ["cup", "smoke", "food"],
  studio: ["cup", "food", "cup"],
  tavern: ["drink", "drink", "smoke", "food"],
  home: ["food", "cup", null],
  plaza: ["smoke", null],
  shop: ["food", null],
};
const DOOR_SPEED = 4;
const OUTLINE = "#1a1a1a";
const REROUTE_AFTER = 0.6; // seconds blocked before looking for a way around
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

  let slot = null;

  // Rooms have walls and two double doors, each 2 tiles wide so a 16 px sprite never brushes the wall ends: one in
  // the middle of the bottom row (entered from below) and one at the bottom of the right wall (entered from the
  // right). dx/dy point out of the room.
  const rooms = {};
  for (const [id, loc] of Object.entries(def.locations)) {
    const r = loc.rect;
    const bottom = r.y + r.height - 1;
    const bx = r.x + Math.floor((r.width - 2) / 2);
    const right = r.x + r.width - 1;
    rooms[id] = {
      rect: r,
      doors: [
        { tiles: [{ x: bx, y: bottom }, { x: bx + 1, y: bottom }], dx: 0, dy: 1, open: 0 },
        { tiles: [{ x: right, y: bottom - 1 }, { x: right, y: bottom }], dx: 1, dy: 0, open: 0 },
      ],
    };
  }
  const roomAt = (t) =>
    Object.values(rooms).find(
      ({ rect: r }) => t.x >= r.x && t.x < r.x + r.width && t.y >= r.y && t.y < r.y + r.height,
    );
  const sameTile = (a, b) => a.x === b.x && a.y === b.y;
  const key = (t) => t.y * def.map.width + t.x;
  const isDoor = (room, t) => room.doors.some((d) => d.tiles.some((dt) => sameTile(dt, t)));

  // The bank teller stands behind the counter on this column of the top row.
  const tellerCol = (w) => Math.floor((w - 1) / 2);
  const tellerTile = (loc) => ({ x: loc.rect.x + tellerCol(loc.rect.width), y: loc.rect.y });
  const isTeller = (cid) => def.characters.find((c) => c.id === cid)?.sprite?.style === "teller";
  // The staff gate: a swing gate in the counter (row 1) right of the teller's window. The teller walks from her
  // tile, along the staff row to the tile above the gate, then through it. Only she goes back there.
  const gateCol = (w) => tellerCol(w) + 1;
  const staffTiles = (loc) => {
    const { x, y } = loc.rect;
    const g = x + gateCol(loc.rect.width);
    return [tellerTile(loc), { x: g, y }, { x: g, y: y + 1 }];
  };
  const gateFront = (loc) => ({ x: loc.rect.x + gateCol(loc.rect.width), y: loc.rect.y + 2 });

  // The bank's two one-person service spots: the teller window (below her, past the counter) and the ATM front.
  const windowTile = (loc) => ({ x: loc.rect.x + tellerCol(loc.rect.width), y: loc.rect.y + 2 });
  const atmTile = (loc) => ({ x: loc.rect.x + 1, y: loc.rect.y + 2 });
  // The grocery's two checkout lanes: a counter with a card terminal on row 1 at these columns, the shopper below it.
  const laneCols = (w) => [1, w - 2];
  const laneTile = (loc, n) => ({ x: loc.rect.x + laneCols(loc.rect.width)[n], y: loc.rect.y + 2 });

  // Up to 4 cubicles in a studio, each 3 tiles wide × 2 tall and open at the front (bottom): desk row on top, the
  // chair in the middle of the row below, a drawer pedestal and a desk return either side of it. Two columns, two
  // rows, with aisles between them and a free entry row below each.
  function cubicles(loc, room) {
    const { width: w, height: h } = loc.rect;
    const out = [];
    for (const r0 of [1, 4]) {
      for (const c0 of [1, w - 4]) {
        if (c0 < 1 || c0 + 3 > w || r0 + 2 >= h || (c0 !== 1 && c0 < 5)) continue;
        if (out.some((o) => o.c0 === c0 && o.r0 === r0)) continue;
        let clear = true;
        for (let dy = 0; dy < 3; dy++) {
          for (let dx = 0; dx < 3; dx++) if (isDoor(room, { x: loc.rect.x + c0 + dx, y: loc.rect.y + r0 + dy })) clear = false;
        }
        if (clear) out.push({ c0, r0 });
      }
    }
    return out;
  }

  // Furniture tiles nobody may stand on or walk over (never a door). Must match what drawRoom draws.
  // Every room's top row is kept clear too: sprites are 24 px tall, so a head there would cover the back wall.
  function furnitureTiles(loc, room) {
    const { x, y, width: w, height: h } = loc.rect;
    const out = [];
    const add = (cx, cy) => {
      if (cx >= 0 && cx < w && cy >= 0 && cy < h) out.push({ x: x + cx, y: y + cy });
    };
    for (let c = 0; c < w; c++) add(c, 0);
    switch (loc.type) {
      case "office": // desk (2 tiles) and filing cabinet along the back wall
        add(0, 0);
        add(1, 0);
        add(w - 1, 0);
        break;
      case "home": // bed (2 tiles) and nightstand
        add(0, 0);
        add(1, 0);
        add(2, 0);
        break;
      case "studio": // cubicles: everything but the chair tile
        for (const { c0, r0 } of cubicles(loc, room)) {
          for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 3; dx++) if (!(dx === 1 && dy === 1)) add(c0 + dx, r0 + dy);
        }
        break;
      case "workshop": // bench rows with a free aisle tile at each end; lower benches also leave a gap in the door column
        for (let r = 0; r < h; r += 2) {
          for (let c = 1; c < w - 1; c++) if (r === 0 || !room.doors[0].tiles.some((t) => t.x === x + c)) add(c, r);
        }
        break;
      case "tavern": // bar along the back wall, a table at each end of the middle row
        for (let c = 0; c < w; c++) add(c, 0);
        if (h > 2 && w > 2) {
          add(0, 1);
          add(w - 1, 1);
        }
        break;
      case "plaza": // fountain
        add(Math.floor(w / 2), Math.floor(h / 2));
        break;
      case "bank": // staff row (teller's tile and the walk to the gate are free; vault on the last tile), counter
        // below it with the staff gate, ATM on the left
        for (let c = 0; c < w; c++) add(c, 1);
        if (h > 2) add(0, 2);
        return out.filter((t) => !isDoor(room, t) && !staffTiles(loc).some((s) => sameTile(s, t)));
      case "shop": // shelves along the back wall (top row), a checkout counter per lane on row 1
        for (const c of laneCols(w)) add(c, 1);
        break;
      default:
        break;
    }
    return out.filter((t) => !isDoor(room, t));
  }
  const blocked = new Set();
  for (const [id, loc] of Object.entries(def.locations)) {
    for (const t of furnitureTiles(loc, rooms[id])) blocked.add(key(t));
  }
  const staff = new Set();
  for (const loc of Object.values(def.locations)) if (loc.type === "bank") for (const t of staffTiles(loc)) staff.add(key(t));
  // Seats (chairs and stools) by tile key -> the way a seated character faces. Must match what drawRoom draws.
  const seats = new Map();
  for (const [id, loc] of Object.entries(def.locations)) {
    const { x, y, width: w, height: h } = loc.rect;
    const seat = (cx, cy, facing) => {
      const t = { x: x + cx, y: y + cy };
      if (cx >= 0 && cx < w && cy >= 0 && cy < h && !isDoor(rooms[id], t) && !blocked.has(key(t))) seats.set(key(t), facing);
    };
    if (loc.type === "office" && h > 1) seat(0, 1, "back");
    if (loc.type === "studio") for (const { c0, r0 } of cubicles(loc, rooms[id])) seat(c0 + 1, r0 + 1, "back");
    if (loc.type === "workshop") {
      // a seat only where there is a bench directly above it (none in the door-column gap)
      for (let r = 1; r < h; r += 2) {
        for (let c = 2; c < w - 1; c += 2) if (blocked.has(key({ x: x + c, y: y + r - 1 }))) seat(c, r, "back");
      }
    }
    if (loc.type === "tavern" && h > 2 && w > 3) {
      seat(1, 1, "left");
      seat(w - 2, 1, "right");
    }
    if (loc.type === "bank" && h > 3) for (let c = 0; c < 3; c++) seat(c, h - 2, "back"); // waiting chairs
  }
  // Free floor tiles where characters stand: seats first, then the rest in reading order (never furniture or a
  // doorway). In a bank the teller window and the ATM front are kept for whoever the sim puts there (`spot`);
  // everyone else waits on the chairs first.
  const standCells = {};
  const standFacing = new Map(); // tile key -> the way someone standing idle there faces
  for (const [id, loc] of Object.entries(def.locations)) {
    const { x, y, width, height } = loc.rect;
    const all = [];
    for (let j = 0; j < height; j++) for (let i = 0; i < width; i++) all.push({ x: x + i, y: y + j });
    let free = all.filter((t) => !blocked.has(key(t)) && !isDoor(rooms[id], t));
    if (loc.type === "bank") {
      standFacing.set(key(windowTile(loc)), "back");
      standFacing.set(key(atmTile(loc)), "left");
      // nobody idles behind the counter or in front of the gate (that would shut the teller in)
      free = free.filter(
        (t) => !sameTile(t, windowTile(loc)) && !sameTile(t, atmTile(loc)) && !staff.has(key(t)) && !sameTile(t, gateFront(loc)),
      );
    }
    if (loc.type === "shop") {
      for (const n of [0, 1]) standFacing.set(key(laneTile(loc, n)), "back");
      free = free.filter((t) => !sameTile(t, laneTile(loc, 0)) && !sameTile(t, laneTile(loc, 1)));
    }
    const ordered = [...free.filter((t) => seats.has(key(t))), ...free.filter((t) => !seats.has(key(t)))];
    standCells[id] = ordered.length ? ordered : all;
  }

  function canStep(a, b) {
    if (b.x < 0 || b.y < 0 || b.x >= def.map.width || b.y >= def.map.height) return false;
    if (blocked.has(key(b))) return false;
    const ra = roomAt(a);
    const rb = roomAt(b);
    if (!rb && !road.has(key(b))) return false; // outside, only the roads are walkable
    if (ra === rb) return true;
    // Crossing a wall: only through one of that room's doors, straight out the way it opens.
    return [
      [ra, a, b],
      [rb, b, a],
    ].every(
      ([room, inside, outside]) =>
        !room ||
        room.doors.some(
          (d) =>
            d.tiles.some((dt) => sameTile(inside, dt)) && outside.x === inside.x + d.dx && outside.y === inside.y + d.dy,
        ),
    );
  }

  // Shortest walk over tiles, never through a wall, never off the roads outside, and never onto an `avoid` tile
  // (except `to`). Returns the tiles after `from` up to `to`, or null if there is no way.
  function route(from, to, avoid = new Set()) {
    const dist = new Map([[key(from), 0]]);
    const prev = new Map([[key(from), null]]);
    const buckets = [[from]]; // buckets[d] = tiles reached at cost d
    for (let d = 0; d < buckets.length; d++) {
      for (const cur of buckets[d] || []) {
        if (dist.get(key(cur)) !== d) continue;
        if (sameTile(cur, to)) {
          const path = [];
          for (let t = cur; prev.get(key(t)); t = prev.get(key(t))) path.unshift(t);
          return path;
        }
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const n = { x: cur.x + dx, y: cur.y + dy };
          // never through someone's chair: a seat tile is only entered by whoever is going to sit in it
          if (!canStep(cur, n) || ((avoid.has(key(n)) || seats.has(key(n))) && !sameTile(n, to))) continue;
          const nd = d + 1;
          if (dist.has(key(n)) && dist.get(key(n)) <= nd) continue;
          dist.set(key(n), nd);
          prev.set(key(n), cur);
          (buckets[nd] ||= []).push(n);
        }
      }
    }
    return null;
  }

  // Roads: the street grid laid out in world.json (map.roads). Outside rooms, only road tiles are walkable.
  const road = new Set();
  for (const r of def.map.roads) {
    for (let x = r.x; x < r.x + r.width; x++) for (let y = r.y; y < r.y + r.height; y++) road.add(key({ x, y }));
  }
  const roadTiles = [...road].map((k) => ({ k, x: k % def.map.width, y: Math.floor(k / def.map.width) }));

  // Sprites are 16×24: centred on the tile with their feet on its bottom edge (the head rises into the tile above).
  const anchor = (t) => ({ x: t.x * ts + (ts - 16) / 2, y: t.y * ts + ts - 24 });
  const onAnchor = (ch) => {
    const a = anchor(ch.tile);
    return ch.x === a.x && ch.y === a.y;
  };
  // Tiles the given characters stand on, or are already stepping into.
  function takenBy(others) {
    const taken = new Set();
    for (const o of others) {
      taken.add(key(o.tile));
      if (o.path.length && !onAnchor(o)) taken.add(key(o.path[0]));
    }
    return taken;
  }
  // `o` was bumped by `mover`: it turns and steps to a free side tile (off the mover's path if it can), then
  // walks on to its goal (an idle sprite walks back to its spot). False when every side tile is taken.
  function stepAside(o, mover) {
    const taken = takenBy([...chars.values()].filter((c) => c !== o && c.visible));
    const sides = [[1, 0], [-1, 0], [0, 1], [0, -1]]
      .map(([dx, dy]) => ({ x: o.tile.x + dx, y: o.tile.y + dy }))
      .filter((n) => canStep(o.tile, n) && !taken.has(key(n)) && !seats.has(key(n)));
    const n = sides.find((s) => !mover.path.some((t) => sameTile(t, s))) || sides[0];
    if (!n) return false;
    o.path = [n, ...(route(n, o.goal) || [])];
    o.wait = 0;
    return true;
  }

  const locLabels = new Map(); // location id -> its map label
  for (const [id, loc] of Object.entries(def.locations)) {
    const el = document.createElement("div");
    el.className = "loc-label";
    el.textContent = loc.name;
    el.style.left = `${((loc.rect.x * ts) / W) * 100}%`;
    el.style.top = `${((loc.rect.y * ts) / H) * 100}%`;
    overlay.appendChild(el);
    locLabels.set(id, el);
  }

  const chars = new Map();
  const seatOwner = new Map(); // office/studio seat key -> the character whose computer it is
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
      teller: isTeller(c.id),
      guard: !!c.guard,
      index: chars.size,
      item: null,
      x: 0,
      y: 0,
      tile: null,
      goal: null,
      path: [],
      wait: 0,
      dir: "front",
      placed: false,
      visible: false,
      moving: false,
      label,
      bubble,
      bubbleTimer: null,
      pending: null, // { text, other, mover, until }: speech waiting for the walk over to end
      meet: null, // { back, loc }: walked over to talk; `back` is the spot to return to
      holdUntil: 0, // spoken to: stands still (keeping its path) until this time
      inTalk: false, // in a talk the server has open: a live hold keeps going until it closes
    });
  }

  function setState(state) {
    slot = state.clock.slot;
    for (const c of def.characters) {
      const label = locLabels.get(c.home);
      if (!label) continue;
      const owned = state.characters[c.id]?.stats?.house === "Bank-owned";
      label.textContent = def.locations[c.home].name + (owned ? " (Bank-owned)" : "");
    }
    const groups = {};
    for (const [cid, cs] of Object.entries(state.characters)) {
      if (!chars.has(cid)) continue;
      chars.get(cid).health = cs.stats?.health;
      (groups[cs.location] ||= []).push(cid);
    }
    const standing = [...chars.values()].filter((o) => o.visible && !o.path.length);
    for (const ch of chars.values()) ch.visible = false;
    for (const [locId, ids] of Object.entries(groups)) {
      const loc = def.locations[locId];
      if (!loc) continue;
      ids.sort((a, b) => def.turn_order.indexOf(a) - def.turn_order.indexOf(b));
      const cells = standCells[locId];
      const items = ITEMS[loc.type] || [null];
      const slotIndex = Math.max(0, def.slots.indexOf(slot));
      let i = 0;
      // Office/studio: everyone keeps their own computer (the seat they first took); newcomers take a free one
      // nobody owns, else the floor. Nobody sits at someone else's desk.
      const ownDesks = loc.type === "office" || loc.type === "studio";
      const deskSeats = ownDesks ? cells.filter((t) => seats.has(key(t))) : [];
      const floor = ownDesks ? cells.filter((t) => !seats.has(key(t))) : cells;
      const deskFor = (cid) => {
        const own = deskSeats.find((t) => seatOwner.get(key(t)) === cid);
        if (own) return own;
        const free = deskSeats.find((t) => !seatOwner.has(key(t)));
        if (!free) return null;
        seatOwner.set(key(free), cid);
        return free;
      };
      ids.forEach((cid) => {
        const ch = chars.get(cid);
        ch.visible = true;
        ch.item = items[(ch.index + slotIndex) % items.length];
        if (loc.type === "bank" && isTeller(cid) && !ch.placed) {
          // The teller first appears behind the counter; later she walks there through the staff gate.
          ch.tile = ch.goal = tellerTile(loc);
          ({ x: ch.x, y: ch.y } = anchor(ch.tile));
          ch.path = [];
          ch.placed = true;
          return;
        }
        // Walked over to talk: stay beside them until the talk ends (then speak() sends it back).
        if (ch.meet && ch.meet.loc === locId) return;
        ch.meet = null;
        // Bank: whoever the sim put at the teller window or the ATM goes there; everyone else (waiting or done)
        // takes the chairs first, then the floor.
        const spot = state.characters[cid].spot;
        const target =
          loc.type === "bank" && ch.teller
            ? tellerTile(loc)
            : loc.type === "bank" && spot === "teller"
            ? windowTile(loc)
            : loc.type === "bank" && spot === "atm"
              ? atmTile(loc)
              : loc.type === "shop" && (spot === "checkout_1" || spot === "checkout_2")
                ? laneTile(loc, spot === "checkout_1" ? 0 : 1)
                : (ownDesks && !(loc.type === "studio" && ch.guard) && deskFor(cid)) || floor[i++ % floor.length] || cells[0];
        if (!ch.placed) {
          ch.tile = ch.goal = target;
          ({ x: ch.x, y: ch.y } = anchor(target));
          ch.placed = true;
        } else if (!sameTile(ch.goal, target)) {
          // Finish the tile it is stepping into, then walk the route from there, around anyone standing still.
          const start = ch.path.length ? ch.path[0] : ch.tile;
          const rest = route(start, target, takenBy(standing.filter((o) => o !== ch)));
          if (rest) {
            ch.goal = target;
            ch.path = ch.path.length ? [start, ...rest] : rest;
          }
          // No way through: stay put; the next state update tries again.
        }
      });
    }
  }

  const locIdAt = (t) => Object.keys(rooms).find((id) => rooms[id] === roomAt(t));

  // `to` (local mode only): who the speaker talks to. The speaker first walks up beside them (to a customer at the
  // bank, the customer walks to the teller's window), and speaks on arrival. Otherwise it speaks at once.
  function showBubble(cid, text, to) {
    const ch = chars.get(cid);
    if (!ch) return;
    const other = to && chars.get(to);
    if (
      other && ch.visible && other.visible && ch.tile && other.tile &&
      roomAt(ch.tile) !== roomAt(other.tile) && (ch.path.length || other.path.length)
    ) {
      // Still on the way over (e.g. a guard walking to someone): hold the line until they share a room, then walk
      // straight up beside them. Speaks anyway after 20 s.
      const until = ch.pending && ch.pending.to === to && ch.pending.text === text ? ch.pending.until : performance.now() + 20000;
      ch.pending = { text, to, cid, until };
      return;
    }
    if (other && ch.visible && other.visible && ch.tile && other.tile && roomAt(ch.tile) === roomAt(other.tile)) {
      const [mover, still] = ch.teller ? [other, ch] : [ch, other];
      other.holdUntil = performance.now() + 6000; // the one spoken to stops walking away
      // where `still` stands once the tile it is stepping into is reached
      const at = still.path.length && !onAnchor(still) ? still.path[0] : still.tile;
      const from = mover.path.length && !onAnchor(mover) ? mover.path[0] : mover.tile;
      const taken = takenBy([...chars.values()].filter((o) => o !== mover && o.visible));
      const beside = still.teller
        ? [windowTile(def.locations[locIdAt(at)])]
        : [[1, 0], [-1, 0], [0, 1], [0, -1]]
            .map(([dx, dy]) => ({ x: at.x + dx, y: at.y + dy }))
            .filter((n) => canStep(at, n) && !seats.has(key(n)));
      let best = null;
      for (const n of beside) {
        if (sameTile(n, from)) {
          best = null; // already beside them
          break;
        }
        if (taken.has(key(n))) continue;
        const path = route(from, n, taken);
        if (path && (!best || path.length < best.path.length)) best = { n, path };
      }
      if (best) {
        mover.meet = { back: mover.meet ? mover.meet.back : mover.goal, loc: locIdAt(at) };
        mover.goal = best.n;
        mover.path = from === mover.tile ? best.path : [from, ...best.path];
      }
      ch.pending = { text, other, mover, until: performance.now() + 8000 };
      return;
    }
    speak(ch, text, null);
  }

  // Show the bubble for 6 s, the pair facing each other (or the nearest sprite in the same room). A sprite that
  // walked over to talk goes back to its spot afterwards.
  function speak(ch, text, other) {
    ch.pending = null;
    ch.bubble.textContent = wrap(text).join("\n");
    ch.bubble.hidden = false;
    const here = ch.tile && roomAt(ch.tile);
    const others = [...chars.values()].filter((o) => o !== ch && o.visible && o.tile && roomAt(o.tile) === here);
    const dist = (o) => Math.abs(o.tile.x - ch.tile.x) + Math.abs(o.tile.y - ch.tile.y);
    const near = other || (ch.visible && ch.tile ? others.sort((a, b) => dist(a) - dist(b))[0] : null);
    if (near) {
      const until = performance.now() + 6000;
      ch.talk = { to: near, until };
      near.talk = { to: ch, until };
    }
    clearTimeout(ch.bubbleTimer);
    ch.bubbleTimer = setTimeout(() => {
      ch.bubble.hidden = true;
      for (const m of [ch, other]) {
        if (!m || !m.meet) continue;
        const back = m.meet.back;
        m.meet = null;
        const from = m.path.length ? m.path[0] : m.tile;
        const rest = m.visible && back && route(from, back, takenBy([...chars.values()].filter((o) => o !== m && o.visible)));
        if (rest) {
          m.goal = back;
          m.path = m.path.length ? [from, ...rest] : rest;
        }
      }
    }, 6000);
  }

  function rect(color, x, y, w, h) {
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, h);
  }

  // Bank counter on row 1: glass with posts and the open teller window, shaded top with the bell and cash tray, and
  // `frontRows` px of the panelled front. Drawn whole before the sprites, and its top 8 px again after them, so the
  // teller standing behind it is hidden from the waist down.
  function bankCounter(loc, frontRows) {
    const px = loc.rect.x * ts;
    const pw = loc.rect.width * ts;
    const cy = (loc.rect.y + 1) * ts;
    const tellerX = px + tellerCol(loc.rect.width) * ts;
    const gx = tellerX + ts; // the staff gate's column, left out of the counter
    const band = (color, y, h) => {
      rect(color, px + 2, y, gx - px - 2, h);
      rect(color, gx + ts, y, px + pw - 2 - gx - ts, h);
    };
    band("#bfe3f0", cy, 3);
    band("#7a9aa8", cy, 1);
    for (let x = px + ts; x < px + pw - 2; x += ts) if (x !== gx && x !== gx + ts) rect("#7a9aa8", x, cy, 1, 3); // glass posts
    rect("#bfe3f0", tellerX + 3, cy, 10, 3); // open teller window
    band("#a0703c", cy + 3, 3);
    band("#c99258", cy + 3, 1);
    band("#6b4423", cy + 6, frontRows);
    for (let x = px + 8; x < px + pw - 2; x += 8) if (x < gx || x >= gx + ts) rect("#5a3a1a", x, cy + 6, 1, frontRows);
    rect("#d4af37", tellerX + 3, cy + 2, 3, 2); // bell
    rect("#9aa0a6", tellerX + 8, cy + 3, 5, 1); // cash tray
    rect("#5aa04a", tellerX + 9, cy + 2, 3, 1);
    // Staff gate: a wooden half-door between two posts, swung back against its left post while someone is in or
    // stepping into the gateway (the floor shows through, and nothing covers the one walking through).
    const g = staffTiles(loc)[2];
    const open = [...chars.values()].some(
      (c) => c.visible && c.tile && (sameTile(c.tile, g) || (c.path.length && sameTile(c.path[0], g))),
    );
    rect("#5a3a1a", gx, cy, 1, 6 + frontRows); // posts
    rect("#5a3a1a", gx + ts - 1, cy, 1, 6 + frontRows);
    if (open) rect("#a0703c", gx + 1, cy, 2, 6 + frontRows);
    else {
      rect("#a0703c", gx + 1, cy + 3, ts - 2, 3 + frontRows);
      rect("#c99258", gx + 1, cy + 3, ts - 2, 1);
      rect("#6b4423", gx + 3, cy + 6, ts - 6, Math.max(1, frontRows - 2)); // inset panel
      rect("#d4af37", gx + ts - 4, cy + 5, 2, 1); // latch
    }
  }

  // Office chair seen from behind on the tile at (tx, ty): cushion, backrest, gas post, star base with wheels.
  function officeChair(tx, ty) {
    rect(SHADOW, tx + 3, ty + 14, 10, 2);
    rect("#3a4a6a", tx + 3, ty + 5, 10, 3);
    rect("#5a6a8a", tx + 3, ty + 5, 10, 1);
    rect("#2f3d58", tx + 4, ty + 8, 8, 3);
    rect("#5a6a8a", tx + 4, ty + 8, 8, 1);
    rect("#555555", tx + 7, ty + 11, 2, 2);
    rect("#333333", tx + 4, ty + 13, 8, 1);
    rect("#111111", tx + 3, ty + 14, 2, 1);
    rect("#111111", tx + 7, ty + 14, 2, 1);
    rect("#111111", tx + 11, ty + 14, 2, 1);
  }

  // A monitor screen. While someone works at it: lines of code that change every 350 ms and a blinking cursor.
  // Otherwise dark with a dim standby light.
  function screen(sx, sy, w, h, active, seed, now) {
    rect(active ? "#1e2a3a" : "#10151c", sx, sy, w, h);
    if (!active) {
      rect("#3a5a3a", sx + w - 2, sy + h - 1, 1, 1);
      return;
    }
    const tick = Math.floor(now / 350);
    const colors = ["#5aa04a", "#4aa3df", "#e6e6e6", "#d98c1a"];
    for (let i = 0; i < h; i++) {
      const n = tick + i + seed;
      const indent = n % 3 === 0 ? 1 : 0;
      const len = 1 + ((n * 7 + seed * 3) % (w - 2));
      rect(colors[n % colors.length], sx + 1 + indent, sy + i, Math.min(len, w - 2 - indent), 1);
    }
    if (Math.floor(now / 500) % 2) rect("#ffffff", sx + w - 2, sy + h - 1, 1, 1);
  }

  // Drawn before the sprites: solid floor, back (top) wall and furniture. `sitting` holds the tile keys of people
  // sitting idle on a seat (they are working, so their monitor is on).
  function drawRoom(loc, room, sitting, now) {
    const { x, y, width, height } = loc.rect;
    const px = x * ts;
    const py = y * ts;
    const pw = width * ts;
    const ph = height * ts;
    rect(loc.color, px, py, pw, ph);
    rect(shade(loc.color, -45), px, py, pw, 2);
    rect(shade(loc.color, -20), px, py, pw, 1); // lit top edge of the back wall

    switch (loc.type) {
      case "office":
        // desk (shaded top, front, knee gap, drawer, legs) with a monitor, keyboard, mouse, papers and a mug;
        // chair in front; filing cabinet in the top-right tile
        rect(SHADOW, px + 3, py + 14, 27, 2);
        rect("#b07a42", px + 2, py + 5, 28, 4);
        rect("#c99258", px + 2, py + 5, 28, 1);
        rect("#7a4f25", px + 2, py + 9, 28, 4);
        rect("#5a3a1a", px + 5, py + 9, 11, 4);
        rect("#8b5a2b", px + 19, py + 9, 10, 4);
        rect("#d4af37", px + 23, py + 11, 2, 1);
        rect("#5a3a1a", px + 2, py + 13, 2, 2);
        rect("#5a3a1a", px + 28, py + 13, 2, 2);
        rect("#222222", px + 5, py + 0, 11, 5);
        screen(px + 6, py + 1, 9, 3, sitting.has(key({ x, y: y + 1 })), x + y, now);
        rect("#444444", px + 9, py + 5, 3, 1);
        rect("#dddddd", px + 6, py + 7, 8, 1);
        rect("#dddddd", px + 16, py + 7, 2, 1);
        rect("#fafafa", px + 19, py + 6, 3, 2);
        rect("#f2f2f2", px + 25, py + 6, 2, 2);
        if (height > 1) {
          // office chair on the tile in front of the desk, seen from behind: cushion, backrest, gas post,
          // star base with wheels
          officeChair(px, py + ts);
        }
        // filing cabinet: lit left edge, three drawers with handles, shadow
        rect(SHADOW, px + pw - 12, py + 14, 10, 1);
        rect("#7d7d7d", px + pw - 12, py + 2, 10, 12);
        rect("#9a9a9a", px + pw - 12, py + 2, 1, 12);
        rect("#9a9a9a", px + pw - 12, py + 2, 10, 1);
        rect("#5a5a5a", px + pw - 12, py + 6, 10, 1);
        rect("#5a5a5a", px + pw - 12, py + 10, 10, 1);
        for (const hy of [4, 8, 12]) rect("#d0d0d0", px + pw - 8, py + hy, 2, 1);
        break;
      case "studio":
        // spritecast-style cubicles (3/4 view, shaded faces, lit trims, soft shadows): carpet, back and side
        // partition panels, an L desk with a live monitor, keyboard, mouse, mug, plant and a pinboard, a drawer
        // pedestal and the chair. Must match cubicles(), furnitureTiles and seats.
        for (const { c0, r0 } of cubicles(loc, room)) {
          const X = px + c0 * ts;
          const Y = py + r0 * ts;
          const fabric = "#5d6d8a";
          const panelSide = "#4a5872";
          const trim = "#9aa6b8";
          rect(shade(loc.color, 12), X + 3, Y + 6, 3 * ts - 6, 2 * ts - 6); // carpet
          // desk along the back, with an L return on the right in the chair row
          rect(SHADOW, X + 4, Y + 15, 3 * ts - 8, 2);
          rect("#b07a42", X + 3, Y + 7, 3 * ts - 6, 5);
          rect("#c99258", X + 3, Y + 7, 3 * ts - 6, 1);
          rect("#7a4f25", X + 3, Y + 12, 3 * ts - 6, 3);
          rect("#b07a42", X + 2 * ts + 1, Y + ts, ts - 4, 10);
          rect("#c99258", X + 2 * ts + 1, Y + ts, 1, 10);
          rect("#7a4f25", X + 2 * ts + 1, Y + ts + 10, ts - 4, 3);
          rect(SHADOW, X + 2 * ts + 1, Y + ts + 13, ts - 4, 2);
          // drawer pedestal under the left end
          rect("#7d7d7d", X + 4, Y + ts + 2, 9, 11);
          rect("#9a9a9a", X + 4, Y + ts + 2, 9, 1);
          rect("#5a5a5a", X + 4, Y + ts + 6, 9, 1);
          rect("#5a5a5a", X + 4, Y + ts + 9, 9, 1);
          rect("#d0d0d0", X + 7, Y + ts + 4, 3, 1);
          rect("#d0d0d0", X + 7, Y + ts + 8, 3, 1);
          rect(SHADOW, X + 4, Y + ts + 13, 9, 2);
          // back panel: fabric with a lit top trim, a pinboard with notes
          rect(fabric, X, Y, 3 * ts, 7);
          rect(trim, X, Y, 3 * ts, 1);
          rect(shade(fabric, -20), X, Y + 6, 3 * ts, 1);
          rect("#c8a26a", X + 2 * ts + 2, Y + 2, 10, 4);
          rect("#fff59d", X + 2 * ts + 3, Y + 3, 3, 2);
          rect("#90caf9", X + 2 * ts + 7, Y + 3, 3, 2);
          rect("#e53935", X + 2 * ts + 4, Y + 3, 1, 1);
          // monitor (live while its owner works at it), keyboard, mouse, mug, potted plant
          rect("#222222", X + ts + 2, Y + 2, 12, 7);
          screen(X + ts + 3, Y + 3, 10, 5, sitting.has(key({ x: x + c0 + 1, y: y + r0 + 1 })), c0 + r0, now);
          rect("#444444", X + ts + 7, Y + 9, 2, 1);
          rect("#dddddd", X + ts + 3, Y + 10, 9, 1);
          rect("#dddddd", X + ts + 13, Y + 10, 1, 1);
          rect("#f2f2f2", X + 2 * ts + 4, Y + 8, 2, 2);
          rect("#8b5a2b", X + 6, Y + 8, 4, 3);
          rect("#3a9d5d", X + 5, Y + 5, 6, 3);
          rect("#5cc07a", X + 6, Y + 5, 2, 1);
          // side panels: darker side face with a lit edge, the full depth of the cubicle
          for (const sx of [X, X + 3 * ts - 3]) {
            rect(panelSide, sx, Y, 3, 2 * ts);
            rect(trim, sx, Y, 3, 1);
            rect(shade(panelSide, 15), sx + (sx === X ? 0 : 2), Y, 1, 2 * ts);
            rect(SHADOW, sx, Y + 2 * ts, 3, 2);
          }
          officeChair(X + ts, Y + ts);
        }
        break;
      case "workshop":
        for (let row = 0; row < height; row += 2) {
          // bench with legs and tools, leaving an aisle tile at each end (lower benches also split around the
          // door-column gap); stools in the row below, never in an aisle
          const rowY = py + row * ts;
          const gapX = room.doors[0].tiles[0].x * ts; // 2-tile gap over the bottom door
          const left = px + ts + 3;
          const right = px + pw - ts - 3;
          const segments =
            row === 0
              ? [[left, right]]
              : [
                  [left, gapX - 1],
                  [gapX + 2 * ts + 1, right],
                ];
          for (const [sx0, sx1] of segments) {
            if (sx1 - sx0 < 6) continue;
            const len = sx1 - sx0;
            // shaded bench top and front, legs, a vice at the start, hammers and wrenches, wood offcuts, shadow
            rect(SHADOW, sx0 + 1, rowY + 14, len, 2);
            rect("#9b7a52", sx0, rowY + 4, len, 4);
            rect("#b8946a", sx0, rowY + 4, len, 1);
            rect("#5c4329", sx0, rowY + 8, len, 3);
            rect("#4a3520", sx0, rowY + 10, len, 1);
            rect("#5c4329", sx0 + 1, rowY + 11, 2, 3);
            rect("#5c4329", sx1 - 3, rowY + 11, 2, 3);
            rect("#555555", sx0 + 2, rowY + 2, 4, 3);
            rect("#888888", sx0 + 2, rowY + 2, 4, 1);
            for (let tx = sx0 + 9; tx + 14 < sx1; tx += 32) {
              rect("#8b5a2b", tx, rowY + 6, 5, 1); // hammer handle
              rect("#888888", tx + 5, rowY + 5, 2, 2); // hammer head
              rect("#9aa0a6", tx + 9, rowY + 6, 4, 1); // wrench
              rect("#9aa0a6", tx + 13, rowY + 5, 1, 3);
            }
            if (len > 14) rect("#d9b48a", sx1 - 10, rowY + 5, 6, 1);
          }
          if (row + 1 < height) {
            for (let sx = px + 2 * ts + 5; sx < px + pw - ts - 6; sx += 32) {
              if (isDoor(room, { x: Math.floor(sx / ts), y: (rowY + ts) / ts })) continue;
              // round stool: seat with a highlight, two splayed legs, footrest
              const sy = rowY + ts;
              rect(SHADOW, sx, sy + 11, 7, 1);
              rect("#6a6a6a", sx, sy + 5, 6, 2);
              rect("#8a8a8a", sx + 1, sy + 5, 4, 1);
              rect("#444444", sx, sy + 7, 1, 4);
              rect("#444444", sx + 5, sy + 7, 1, 4);
              rect("#444444", sx + 1, sy + 9, 4, 1);
            }
          }
        }
        break;
      case "tavern": {
        // bar with bottles and stools along the back wall; a table at each end of the middle row,
        // each with a chair on the tile beside it (facing the table)
        // back shelf with bottles, shaded counter top with taps, panelled counter front, stools
        rect("#5a3a1a", px + 2, py + 5, pw - 4, 1);
        const bottles = ["#3a9d5d", "#c0504d", "#d98c1a", "#3a6bd5"];
        for (let i = 0; i < width; i++) {
          const bx = px + i * ts;
          for (const [k, ox] of [
            [0, 3],
            [1, 9],
          ]) {
            const c = bottles[(i * 2 + k) % bottles.length];
            rect(c, bx + ox, py + 2, 2, 3);
            rect(shade(c, -30), bx + ox + 1, py + 2, 1, 3);
            rect(c, bx + ox, py + 1, 1, 1);
          }
        }
        rect("#a0703c", px + 2, py + 6, pw - 4, 2);
        rect("#c99258", px + 2, py + 6, pw - 4, 1);
        rect("#5a3a1a", px + 2, py + 8, pw - 4, 6);
        for (let gx = px + 6; gx < px + pw - 2; gx += 8) rect("#4a2a10", gx, py + 8, 1, 6);
        for (let i = 0; i < width; i++) {
          const bx = px + i * ts;
          rect("#c0c0c0", bx + 7, py + 4, 1, 2); // tap
          rect("#222222", bx + 7, py + 3, 1, 1);
          rect("#a33a3a", bx + 5, py + 13, 6, 2); // stool
          rect("#c25050", bx + 5, py + 13, 6, 1);
          rect("#3a2a10", bx + 7, py + 15, 2, 1);
        }
        if (height > 2 && width > 2) {
          const ty = py + ts;
          for (const [tx, chairX, inner] of [
            [px, px + ts, "right"],
            [px + pw - ts, px + pw - 2 * ts, "left"],
          ]) {
            // pedestal table: shaded top, rim, post, foot, candle and a mug, shadow
            rect(SHADOW, tx + 3, ty + 14, 10, 2);
            rect("#a0703c", tx + 2, ty + 4, 12, 6);
            rect("#c99258", tx + 2, ty + 4, 12, 1);
            rect("#6b4423", tx + 2, ty + 10, 12, 1);
            rect("#6b4423", tx + 7, ty + 11, 2, 3);
            rect("#4a2a10", tx + 5, ty + 14, 6, 1);
            rect("#f5e6c8", tx + 5, ty + 5, 2, 2);
            rect("#ffb02e", tx + 5, ty + 4, 1, 1);
            rect("#d98c1a", tx + 10, ty + 6, 2, 2);
            rect("#f2f2f2", tx + 10, ty + 5, 2, 1);
            rect(SHADOW, chairX + 2, ty + 14, 11, 1);
            // side-on chair, seat toward the table: slatted backrest, cushioned seat, front leg, crossbar
            const c = (color, rx, ry, w, h) => rect(color, chairX + (inner === "right" ? rx : ts - rx - w), ty + ry, w, h);
            c("#5a3a1a", 10, 2, 2, 12);
            c("#7a5230", 8, 2, 4, 2);
            c("#7a5230", 8, 6, 2, 1);
            c("#7a5230", 2, 8, 10, 2);
            c("#a33a3a", 3, 7, 7, 1);
            c("#5a3a1a", 2, 10, 2, 4);
            c("#4a2a10", 4, 12, 6, 1);
          }
        }
        break;
      }
      case "home":
        // bed: frame, lit headboard, sheet, blanket with a fold and a shade, pillow, foot posts, shadow
        rect(SHADOW, px + 3, py + 15, 27, 1);
        rect("#6b4423", px + 2, py + 2, 28, 13);
        rect("#5a3a1a", px + 2, py + 1, 3, 14);
        rect("#8b5a2b", px + 2, py + 1, 3, 1);
        rect("#eeeeee", px + 5, py + 3, 24, 10);
        rect("#c0504d", px + 11, py + 3, 18, 10);
        rect("#e07a70", px + 11, py + 3, 2, 10);
        rect("#8e3a35", px + 11, py + 12, 18, 1);
        rect("#ffffff", px + 5, py + 4, 6, 8);
        rect("#d8d8d8", px + 5, py + 11, 6, 1);
        rect("#4a2a10", px + 28, py + 13, 2, 2);
        // nightstand with a drawer and knob, lamp with a lit shade
        rect(SHADOW, px + 34, py + 13, 9, 1);
        rect("#7a4f25", px + 34, py + 5, 9, 8);
        rect("#a0703c", px + 34, py + 5, 9, 1);
        rect("#5a3a1a", px + 34, py + 9, 9, 1);
        rect("#d4af37", px + 38, py + 10, 1, 1);
        rect("#d4af37", px + 37, py + 3, 3, 2);
        rect("#ffd27f", px + 36, py + 0, 5, 3);
        rect("#fff0c0", px + 37, py + 0, 2, 1);
        break;
      case "plaza": {
        // fountain: shaded stone basin, water with ripples, spout
        const cx = px + pw / 2;
        const cy = py + ph / 2;
        rect(SHADOW, cx - 11, cy + 11, 24, 2);
        rect("#9a9a9a", cx - 12, cy - 10, 24, 21);
        rect("#bdbdbd", cx - 12, cy - 10, 24, 1);
        rect("#6e6e6e", cx - 12, cy + 10, 24, 1);
        rect("#3a7bd5", cx - 9, cy - 7, 18, 14);
        rect("#7fb2ee", cx - 6, cy - 3, 4, 1);
        rect("#7fb2ee", cx + 2, cy + 2, 4, 1);
        rect("#9a9a9a", cx - 1, cy - 5, 2, 5);
        rect("#bfe3f0", cx - 1, cy - 6, 2, 1);
        break;
      }
      case "bank": {
        // staff row behind the counter (darker floor, a cabinet per tile, vault door on the last tile; the teller's
        // tile stays clear), counter with glass and a bell + cash tray at the teller's window, ATM on row 2.
        // Must match furnitureTiles.
        rect(shade(loc.color, -20), px, py + 2, pw, ts - 2);
        const tellerX = px + tellerCol(width) * ts;
        for (let i = 0; i < width - 1; i++) {
          const bx = px + i * ts;
          if (bx === tellerX || bx === tellerX + ts) continue; // teller's tile and the walk to the gate
          rect("#7a4f25", bx + 3, py + 4, 10, 9);
          rect("#a0703c", bx + 3, py + 4, 10, 1);
          rect("#a0703c", bx + 3, py + 4, 1, 9);
          rect("#5a3a1a", bx + 3, py + 8, 10, 1);
          rect("#d4af37", bx + 7, py + 6, 2, 1);
          rect("#d4af37", bx + 7, py + 10, 2, 1);
        }
        // vault door: ring, bolts, gold wheel
        const vx = px + pw - ts;
        rect("#8a8a8a", vx + 2, py + 2, 12, 12);
        rect("#9e9e9e", vx + 3, py + 3, 10, 10);
        rect("#6a6a6a", vx + 4, py + 4, 8, 8);
        for (const [bx, by] of [
          [3, 3],
          [12, 3],
          [3, 12],
          [12, 12],
        ])
          rect("#555555", vx + bx, py + by, 1, 1);
        rect("#d4af37", vx + 7, py + 6, 2, 4);
        rect("#d4af37", vx + 6, py + 7, 4, 2);
        if (height > 1) {
          bankCounter(loc, 8);
          rect("#5a3a1a", px + 2, py + ts + 13, pw - 4, 1);
        }
        if (height > 2) {
          // ATM: lit top, screen with glare, card slot, keypad, cash slot, shadow
          const ay = py + 2 * ts;
          rect(SHADOW, px + 3, ay + 15, 10, 1);
          rect("#4a5a6a", px + 3, ay + 1, 10, 14);
          rect("#6a7a8a", px + 3, ay + 1, 10, 1);
          rect("#4aa3df", px + 5, ay + 3, 6, 4);
          rect("#8fd0f5", px + 5, ay + 3, 2, 1);
          rect("#222222", px + 5, ay + 8, 3, 1);
          rect("#cccccc", px + 5, ay + 9, 6, 3);
          for (const kx of [6, 8, 10]) rect("#777777", px + kx, ay + 10, 1, 1);
          rect("#222222", px + 5, ay + 13, 6, 1);
        }
        for (let c = 0; c < 3; c++) {
          if (seats.has(key({ x: x + c, y: y + height - 2 }))) officeChair(px + c * ts, py + (height - 2) * ts); // waiting
        }
        break;
      }
      case "jail": {
        // concrete floor with seams, a cot (left two tiles) and a steel toilet (last tile) on the back row, and a
        // small barred window in the back wall. The top row is blocked already (furnitureTiles).
        for (let r = 1; r < height; r++) rect(shade(loc.color, -10), px, py + r * ts, pw, 1);
        for (let c = 1; c < width; c++) rect(shade(loc.color, -10), px + c * ts, py + ts, 1, ph - ts);
        rect(SHADOW, px + 3, py + 14, 26, 2);
        rect("#6b5a45", px + 2, py + 5, 28, 8);
        rect("#8a7458", px + 2, py + 5, 28, 1);
        rect("#d8d8d0", px + 3, py + 6, 7, 4); // pillow
        rect("#4a5a7a", px + 10, py + 6, 19, 6); // blanket
        rect("#3a3a3a", px + 3, py + 13, 2, 2);
        rect("#3a3a3a", px + 27, py + 13, 2, 2);
        const tx = px + pw - ts;
        rect(SHADOW, tx + 4, py + 14, 9, 1);
        rect("#b8bcc2", tx + 5, py + 3, 6, 4); // tank
        rect("#d0d4da", tx + 4, py + 7, 8, 6); // bowl
        rect("#7a7e84", tx + 6, py + 9, 4, 2);
        const wx = px + Math.floor(width / 2) * ts - 6; // barred window
        rect("#1c2433", wx, py + 1, 12, 5);
        for (let b = 2; b < 12; b += 3) rect("#9a9a9a", wx + b, py + 1, 1, 5);
        break;
      }
      case "alley": {
        // dark ground with cracks, two trash bins and a flattened cardboard box on the back row
        rect(shade(loc.color, -12), px + 10, py + ts + 6, 14, 1);
        rect(shade(loc.color, -12), px + pw - 30, py + 2 * ts + 3, 10, 1);
        for (const bx of [px + 3, px + 3 + ts]) {
          rect(SHADOW, bx + 1, py + 14, 11, 2);
          rect("#4f5b4a", bx + 1, py + 4, 10, 10);
          rect("#6a7864", bx, py + 3, 12, 2);
          rect("#3a4436", bx + 3, py + 7, 1, 6);
          rect("#3a4436", bx + 7, py + 7, 1, 6);
        }
        rect("#a07a4a", px + pw - 2 * ts + 2, py + 6, 26, 8);
        rect("#b88e5a", px + pw - 2 * ts + 2, py + 6, 26, 1);
        rect("#7a5a34", px + pw - 2 * ts + 14, py + 6, 1, 8);
        break;
      }
      case "shop": {
        // shelves of produce and goods along the back wall, and a checkout counter per lane (conveyor belt, card
        // terminal with a green screen) on row 1. Must match furnitureTiles.
        const goods = ["#d94a3a", "#e8c547", "#5aa04a", "#e8873a", "#c9d6e0", "#8a5ac0"];
        for (let i = 0; i < width; i++) {
          const sx = px + i * ts;
          rect(SHADOW, sx + 1, py + 14, 14, 2);
          rect("#7a4f25", sx + 1, py + 2, 14, 12);
          rect("#a0703c", sx + 1, py + 2, 14, 1);
          for (const [r, sy] of [
            [0, py + 4],
            [1, py + 9],
          ]) {
            rect("#5a3a1a", sx + 2, sy + 3, 12, 1); // shelf board
            for (let g = 0; g < 4; g++) rect(goods[(i * 2 + r + g) % goods.length], sx + 3 + g * 3, sy, 2, 3);
          }
        }
        for (const c of laneCols(width)) {
          const cx = px + c * ts;
          const cy = py + ts;
          rect(SHADOW, cx + 1, cy + 14, 14, 2);
          rect("#6a6f78", cx + 1, cy + 4, 14, 10); // counter body
          rect("#8a909a", cx + 1, cy + 3, 14, 2); // top
          rect("#2a2a2a", cx + 2, cy + 3, 7, 2); // conveyor belt
          rect("#3a3a3a", cx + 3, cy + 3, 1, 2);
          rect("#3a3a3a", cx + 6, cy + 3, 1, 2);
          rect("#333333", cx + 10, cy, 4, 5); // card terminal
          rect("#5ad46a", cx + 11, cy + 1, 2, 1); // screen
          rect("#bbbbbb", cx + 11, cy + 3, 2, 1); // keys
        }
        break;
      }
      default:
        break;
    }
  }

  // Drawn after the sprites, so nobody shows over them: side walls (right one with the side-door gap), front
  // (bottom) wall with the door gap, and the door leaves, which shrink as they open.
  function drawWalls(loc, room) {
    const { x, y, width, height } = loc.rect;
    const px = x * ts;
    const py = y * ts;
    const pw = width * ts;
    const ph = height * ts;
    const wall = shade(loc.color, -45);
    const lit = shade(loc.color, -20);
    const [bottom, side] = room.doors;
    const doorX = bottom.tiles[0].x * ts; // 2-tile gaps
    // The side door gap gets half a tile of headroom above its 2 door tiles: sprites are 24 px tall, so walking
    // through the upper door tile their head rises 8 px above it.
    const sideY = side.tiles[0].y * ts - ts / 2;
    const sideH = 2 * ts + ts / 2;
    rect(wall, px, py, 2, ph);
    rect(lit, px, py, 1, ph);
    rect(wall, px + pw - 2, py, 2, sideY - py);
    rect(lit, px + pw - 1, py, 1, sideY - py);
    rect(wall, px, py + ph - 2, doorX - px, 2);
    rect(wall, doorX + 2 * ts, py + ph - 2, px + pw - doorX - 2 * ts, 2);
    // double doors: two leaves hinged at the gap's ends, opening from the middle
    const leaf = (d) => Math.round(ts * (1 - d.open));
    rect("#8b5a2b", doorX, py + ph - 2, leaf(bottom), 2);
    rect("#8b5a2b", doorX + 2 * ts - leaf(bottom), py + ph - 2, leaf(bottom), 2);
    rect("#6b4423", doorX + ts - 1, py + ph - 2, bottom.open < 0.05 ? 2 : 0, 2); // seam when shut
    const sideLeaf = Math.round((sideH / 2) * (1 - side.open));
    rect("#8b5a2b", px + pw - 2, sideY, 2, sideLeaf);
    rect("#8b5a2b", px + pw - 2, sideY + sideH - sideLeaf, 2, sideLeaf);
    if (loc.type === "bank" && height > 1) bankCounter(loc, 2); // over the teller's waist
    if (loc.type === "jail") {
      // cell bars across the front row, in front of anyone standing there (not over the bottom door)
      const by = py + ph - ts;
      for (let bx = px + 3; bx < px + pw - 2; bx += 4) {
        if (bx >= doorX && bx < doorX + 2 * ts) continue;
        rect("#9a9a9a", bx, by, 1, ts - 2);
      }
      rect("#7a7a7a", px + 2, by, pw - 4, 1);
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
    // concrete slabs with curbs against the grass
    for (const { k, x, y } of roadTiles) {
      const rx = x * ts;
      const ry = y * ts;
      rect("#a9a8a2", rx, ry, ts, ts);
      for (let sx = 0; sx < ts; sx += 8) {
        for (let sy = 0; sy < ts; sy += 8) {
          rect("#8d8c86", rx + sx, ry + sy, 8, 1); // seams
          rect("#8d8c86", rx + sx, ry + sy, 1, 8);
          rect("#bebdb7", rx + sx + 1, ry + sy + 1, 6, 1); // lit top/left edge
          rect("#bebdb7", rx + sx + 1, ry + sy + 1, 1, 6);
          rect("#9a9993", rx + sx + 1, ry + sy + 7, 7, 1); // shaded bottom/right edge
          rect("#9a9993", rx + sx + 7, ry + sy + 1, 1, 7);
        }
      }
      if (k % 5 === 0) {
        // a small crack
        const cx = rx + ((k * 7) % 10) + 3;
        const cy = ry + ((k * 3) % 10) + 3;
        rect("#85847e", cx, cy, 2, 1);
        rect("#85847e", cx + 2, cy + 1, 1, 1);
      }
      if (k % 7 === 0) rect("#9e9d97", rx + ((k * 11) % 9) + 3, ry + ((k * 5) % 9) + 3, 3, 2); // a stain
      const grass = (gx, gy) =>
        gx >= 0 && gy >= 0 && gx < def.map.width && gy < def.map.height && !road.has(gy * def.map.width + gx) && !roomAt({ x: gx, y: gy });
      if (grass(x, y - 1)) {
        rect("#6f6e69", rx, ry, ts, 1);
        rect("#c9c8c2", rx, ry + 1, ts, 1);
      }
      if (grass(x, y + 1)) {
        rect("#6f6e69", rx, ry + ts - 1, ts, 1);
        rect("#c9c8c2", rx, ry + ts - 2, ts, 1);
      }
      if (grass(x - 1, y)) {
        rect("#6f6e69", rx, ry, 1, ts);
        rect("#c9c8c2", rx + 1, ry, 1, ts);
      }
      if (grass(x + 1, y)) {
        rect("#6f6e69", rx + ts - 1, ry, 1, ts);
        rect("#c9c8c2", rx + ts - 2, ry, 1, ts);
      }
    }
    // A door opens while someone walks through it (on its tile or the one just outside), and closes after.
    const nearDoor = (t, d) =>
      t && d.tiles.some((dt) => sameTile(t, dt) || (t.x === dt.x + d.dx && t.y === dt.y + d.dy));
    for (const room of Object.values(rooms)) {
      for (const d of room.doors) {
        const wanted = [...chars.values()].some(
          (ch) => ch.visible && ch.path.length && (nearDoor(ch.tile, d) || nearDoor(ch.path[0], d)),
        );
        d.open = Math.min(1, Math.max(0, d.open + (wanted ? dt : -dt) * DOOR_SPEED));
      }
    }
    // Speak once the walk over is done (or after 8 s if the way stays blocked). A line still waiting for the pair to
    // share a room (`to`) tries again each frame.
    for (const ch of chars.values()) {
      const p = ch.pending;
      if (p && p.to) {
        if (now > p.until) speak(ch, p.text, null);
        else showBubble(p.cid, p.text, p.to);
      } else if (p && (!p.mover.path.length || now > p.until)) speak(ch, p.text, p.other);
    }
    const sitting = new Set(
      [...chars.values()].filter((c) => c.visible && !c.path.length && c.tile && seats.has(key(c.tile))).map((c) => key(c.tile)),
    );
    for (const [id, loc] of Object.entries(def.locations)) drawRoom(loc, rooms[id], sitting, now);

    const vis = [];
    const inBed = new Set(); // home rooms whose bed is taken this frame
    for (const ch of chars.values()) {
      if (!ch.visible) {
        ch.label.hidden = true;
        ch.bubble.hidden = true;
        ch.moving = false;
        continue;
      }
      // Walk the path tile by tile (x first, then y, toward each next tile), never into a tile someone else
      // stands on or is stepping into. Blocked: the sprite in the way steps aside; else wait, then look for a
      // way around. Nobody walks through anyone.
      // Spoken to: stand still (path kept) until 6 s after the talk ends or after the last line to it. An open talk
      // keeps a live hold going; it never revives an expired one. Walking up to talk (`meet`) is never held.
      if (ch.inTalk && now < ch.holdUntil) ch.holdUntil = now + 6000;
      let step = SPEED * dt;
      while (step > 0 && ch.path.length) {
        if (onAnchor(ch) && !ch.meet && now < ch.holdUntil) break;
        if (onAnchor(ch)) {
          const taken = takenBy([...chars.values()].filter((o) => o !== ch && o.visible));
          if (taken.has(key(ch.path[0]))) {
            const o = [...chars.values()].find(
              (c) => c !== ch && c.visible && sameTile(c.tile, ch.path[0]) && onAnchor(c),
            );
            // Push aside only someone idle or stuck too; someone walking off with a free next tile is just waited for.
            if (o && (!o.path.length || takenBy([...chars.values()].filter((c) => c !== o && c.visible)).has(key(o.path[0])))) {
              stepAside(o, ch);
            }
            ch.wait += dt;
            if (ch.wait >= REROUTE_AFTER) {
              const around = route(ch.tile, ch.goal, taken);
              if (around && around.length && !taken.has(key(around[0]))) {
                ch.path = around;
                ch.wait = 0;
                continue;
              }
            }
            break;
          }
        }
        ch.wait = 0;
        const a = anchor(ch.path[0]);
        const dx = a.x - ch.x;
        const dy = a.y - ch.y;
        if (dx) ch.dir = dx > 0 ? "right" : "left";
        else if (dy) ch.dir = dy > 0 ? "front" : "back";
        // Close to a corner: face the diagonal between this leg and the next one.
        if (ch.path.length >= 2 && Math.abs(dx) + Math.abs(dy) <= 6) {
          const [n0, n1] = ch.path;
          const next = n1.x !== n0.x ? (n1.x > n0.x ? "right" : "left") : n1.y > n0.y ? "front" : "back";
          if (next !== ch.dir) ch.dir = mixDir(ch.dir, next);
        }
        const mx = Math.min(Math.abs(dx), step);
        ch.x += Math.sign(dx) * mx;
        step -= mx;
        const my = Math.min(Math.abs(dy), step);
        ch.y += Math.sign(dy) * my;
        step -= my;
        if (ch.x === a.x && ch.y === a.y) ch.tile = ch.path.shift();
      }
      ch.moving = ch.path.length > 0;
      // Idle on a seat: sit facing the table/desk/bench. Idle elsewhere: stand facing front, or toward the one
      // it is talking with. At Night the first idle sprite in a home lies in its bed (drawn on the pillow).
      ch.pose = ch.moving ? "walk" : seats.has(key(ch.tile)) ? "sit" : "stand";
      if (!ch.moving) ch.dir = ch.pose === "sit" ? seats.get(key(ch.tile)) : standFacing.get(key(ch.tile)) || "front";
      ch.drawX = ch.x;
      ch.drawY = ch.y;
      const home = !ch.moving && slot === "Night" ? Object.entries(rooms).find(([, r]) => r === roomAt(ch.tile)) : null;
      const bedType = home && def.locations[home[0]].type;
      if ((bedType === "home" || bedType === "alley") && !inBed.has(home[0])) {
        inBed.add(home[0]);
        ch.pose = "sleep";
        ch.drawX = home[1].rect.x * ts + 5;
        ch.drawY = home[1].rect.y * ts + 4;
      } else if (ch.teller && roomAt(ch.tile) && ch.y < anchor({ x: 0, y: roomAt(ch.tile).rect.y + 1 }).y) {
        // the teller on the staff row behind the counter: drawn 8 px lower so her head clears the back wall (easing
        // to 0 as she steps down into the gate); the counter top is drawn over her waist afterwards
        ch.drawY += Math.min(8, Math.max(0, (anchor({ x: 0, y: roomAt(ch.tile).rect.y + 1 }).y - ch.y) / 2));
      } else if (ch.pose === "stand" && ch.talk && now < ch.talk.until && ch.talk.to.visible) {
        const dx = ch.talk.to.x - ch.x;
        const dy = ch.talk.to.y - ch.y;
        const h = dx > 0 ? "right" : "left";
        const v = dy > 0 ? "front" : "back";
        if (Math.abs(dx) >= 8 && Math.abs(dy) >= 8) ch.dir = `${v}-${h}`;
        else ch.dir = Math.abs(dx) >= Math.abs(dy) ? h : v;
      }
      vis.push(ch);
    }
    // Sprites and room walls drawn in one depth order (by the y of their bottom edge): someone below a building is
    // drawn after its walls (head in front), someone inside before them (feet behind the front wall).
    const drawList = [
      ...vis.map((ch) => ({ depth: ch.drawY + (ch.pose === "sleep" ? 8 : 24), ch })),
      ...Object.entries(def.locations).map(([id, loc]) => ({
        depth: (loc.rect.y + loc.rect.height) * ts + 0.5,
        walls: () => drawWalls(loc, rooms[id]),
      })),
    ].sort((a, b) => a.depth - b.depth);
    for (const item of drawList) {
      if (item.walls) {
        item.walls();
        continue;
      }
      const ch = item.ch;
      const sx = Math.round(ch.drawX);
      const sy = Math.round(ch.drawY);
      const opts = {
        frame: ch.moving ? Math.floor(now / WALK_FRAME_MS) % 4 : 0,
        dir: ch.dir,
        pose: ch.pose,
        item: ch.moving ? null : ch.item,
        t: now / 1000 + ch.index * 1.3,
      };
      // soft shadow under the feet (smaller when sitting, none in bed or behind the counter)
      if (ch.pose === "sit") rect(SHADOW, sx + 4, sy + 23, 8, 2);
      else if (ch.pose !== "sleep" && !(ch.teller && ch.drawY !== ch.y)) {
        rect(SHADOW, sx + 3, sy + 23, 10, 2);
        rect(SHADOW, sx + 5, sy + 25, 6, 1);
      }
      for (const [ox, oy] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
        drawSprite(ctx, ch.look, sx + ox, sy + oy, { ...opts, solid: OUTLINE });
      }
      drawSprite(ctx, ch.look, sx, sy, opts);
      if (typeof ch.health === "number" && ch.pose !== "sleep") {
        // health bar over the head: green above 50, yellow above 20, else red
        const hp = Math.max(0, Math.min(100, ch.health));
        rect(OUTLINE, sx, sy - 5, 16, 4);
        rect("#3a1010", sx + 1, sy - 4, 14, 2);
        rect(hp > 50 ? "#3ec46d" : hp > 20 ? "#e0b02a" : "#e04040", sx + 1, sy - 4, Math.round((14 * hp) / 100), 2);
      }
      ch.label.hidden = false;
      ch.label.style.left = `${((ch.drawX + 8) / W) * 100}%`;
      ch.label.style.top = `${((ch.drawY + 24) / H) * 100}%`;
      ch.bubble.style.left = `${((ch.drawX + 8) / W) * 100}%`;
      ch.bubble.style.top = `${(ch.drawY / H) * 100}%`;
    }
    if (slot === "Afternoon") rect("rgba(255,160,0,0.08)", 0, 0, W, H);
    else if (slot === "Night") rect("rgba(10,10,60,0.35)", 0, 0, W, H);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // The server opened or closed a talk between the pair.
  function setTalk(pair, open) {
    for (const cid of pair) if (chars.has(cid)) chars.get(cid).inTalk = open;
  }

  return { setState, showBubble, setTalk };
}
