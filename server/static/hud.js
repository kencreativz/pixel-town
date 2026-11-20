import { resolveSprite } from "/static/sprites.js";

const ICONS = { Morning: "🌅", Afternoon: "☀️", Night: "🌙" };
const BADGES = { llm: "LLM", scripted: "NPC", hybrid: "Hybrid" };

export function createHud(def, worlds, worldId, onControl) {
  const $ = (id) => document.getElementById(id);
  const chat = $("chat");
  const colors = {};
  for (const c of def.characters) colors[c.id] = resolveSprite(c.sprite).outfit;

  $("world-name").textContent = def.name;

  const select = $("world-select");
  if (worlds.length > 1) {
    for (const w of worlds) {
      const o = document.createElement("option");
      o.value = w.id;
      o.textContent = w.name;
      o.selected = w.id === worldId;
      select.appendChild(o);
    }
    select.hidden = false;
    select.addEventListener("change", () => {
      location.search = `?world=${encodeURIComponent(select.value)}`;
    });
  }

  const banner = $("banner");
  $("banner-dismiss").addEventListener("click", () => {
    banner.hidden = true;
  });

  let token = "";
  for (const btn of document.querySelectorAll("#admin button[data-action]")) {
    btn.addEventListener("click", async () => {
      token = $("admin-token").value;
      const res = await onControl(btn.dataset.action, token);
      $("admin-error").textContent = res.ok ? "" : res.detail;
    });
  }

  function line(entry) {
    const div = document.createElement("div");
    div.className = "chat-line";
    const name = document.createElement("span");
    name.textContent = entry.name;
    if (entry.speaker === "system") div.classList.add("system");
    else if (colors[entry.speaker]) name.style.color = colors[entry.speaker];
    div.append(name, ": ", document.createTextNode(entry.text));
    return div;
  }

  const opened = new Set(); // character ids whose card the viewer unfolded (cards start folded)

  function render(state) {
    const { day, slot } = state.clock;
    $("clock").textContent = `${ICONS[slot] || "🕒"} Day ${day} — ${slot}`;

    const globals = $("globals");
    globals.replaceChildren();
    for (const [k, v] of Object.entries(state.globals)) {
      const row = document.createElement("div");
      row.className = "global-row";
      row.textContent = `${k}: ${v}`;
      globals.appendChild(row);
    }

    const cards = $("characters");
    cards.replaceChildren();
    for (const c of def.characters) {
      const cs = state.characters[c.id];
      const card = document.createElement("details");
      card.className = "card";
      card.open = opened.has(c.id);
      card.addEventListener("toggle", () => {
        if (card.open) opened.add(c.id);
        else opened.delete(c.id);
      });
      const head = document.createElement("summary");
      head.className = "card-head";
      const nm = document.createElement("strong");
      nm.textContent = c.name;
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = BADGES[c.kind] || c.kind;
      head.append(nm, badge);
      card.appendChild(head);
      if (cs) {
        const loc = document.createElement("div");
        loc.className = "card-loc";
        loc.textContent = def.locations[cs.location]?.name || cs.location;
        card.appendChild(loc);
        for (const [k, v] of Object.entries(cs.stats)) {
          const row = document.createElement("div");
          const list = (o) => (k === "owes" ? Object.entries(o).map(([id, n]) => `${id} $${n}`) : Object.keys(o));
          row.textContent = `${k}: ${v && typeof v === "object" ? list(v).join(", ") || "none" : v}`;
          card.appendChild(row);
        }
      }
      cards.appendChild(card);
    }
  }

  function setChat(entries) {
    chat.replaceChildren(...entries.slice(-50).map(line));
    chat.scrollTop = chat.scrollHeight;
  }

  function appendChat(entry) {
    const atBottom = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 4;
    chat.appendChild(line(entry));
    while (chat.children.length > 50) chat.removeChild(chat.firstChild);
    if (atBottom) chat.scrollTop = chat.scrollHeight;
  }

  function showNotice(level, text) {
    banner.className = `banner ${level === "warn" ? "warn" : "info"}`;
    $("banner-text").textContent = text;
    banner.hidden = false;
  }

  function setConnected(on) {
    $("conn").className = `dot ${on ? "on" : "off"}`;
  }

  return { render, setChat, appendChat, showNotice, setConnected };
}
