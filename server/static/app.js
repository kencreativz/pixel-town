import { createHud } from "/static/hud.js";
import { createRenderer } from "/static/renderer.js";

const worlds = await (await fetch("/api/worlds")).json();
const worldId = new URLSearchParams(location.search).get("world") || worlds[0].id;
const base = `/api/worlds/${encodeURIComponent(worldId)}`;

const defRes = await fetch(`${base}/definition`);
if (!defRes.ok) {
  const banner = document.getElementById("banner");
  banner.className = "banner warn";
  document.getElementById("banner-text").textContent = `Could not load world: ${defRes.status} ${defRes.statusText}`;
  banner.hidden = false;
  document.getElementById("banner-dismiss").addEventListener("click", () => {
    banner.hidden = true;
  });
} else {
  const def = await defRes.json();
  const renderer = createRenderer(def, document.getElementById("map"), document.getElementById("overlay"));

  async function onControl(action, token) {
    const res = await fetch(`${base}/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": token },
      body: JSON.stringify({ action }),
    });
    if (res.ok) return { ok: true, detail: "" };
    let detail = res.statusText || String(res.status);
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep status text
    }
    return { ok: false, detail };
  }

  const hud = createHud(def, worlds, worldId, onControl);

  let state = null;
  let chat = [];

  function update() {
    renderer.setState(state);
    hud.render(state);
  }

  function pushChat(entry) {
    chat.push(entry);
    chat = chat.slice(-50);
    hud.appendChat(entry);
  }

  const es = new EventSource(`${base}/stream`);
  es.onopen = () => hud.setConnected(true);
  es.onerror = () => hud.setConnected(false);
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "snapshot") {
      state = ev.state;
      chat = ev.log.map(({ speaker, name, text }) => ({ speaker, name, text })).slice(-50);
      hud.setChat(chat);
      update();
      return;
    }
    if (!state) return;
    switch (ev.type) {
      case "slot_start":
        state.clock.day = ev.day;
        state.clock.slot = ev.slot;
        update();
        break;
      case "speech":
        pushChat({ speaker: ev.speaker, name: ev.name, text: ev.text });
        renderer.showBubble(ev.speaker, ev.text);
        break;
      case "system":
        pushChat({ speaker: "system", name: "System", text: ev.text });
        break;
      case "move":
        if (state.characters[ev.character]) {
          state.characters[ev.character].location = ev.to;
          update();
        }
        break;
      case "state":
        state = ev.state;
        update();
        break;
      case "notice":
        hud.showNotice(ev.level, ev.text);
        break;
      default:
        break;
    }
  };
}
