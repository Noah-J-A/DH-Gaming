// NEXUS frontend — plain JS, no build step
const $ = (id) => document.getElementById(id);
const chatLog = $("chat-log");
const input = $("chat-input");
const CORRUPT = "▓▒░█▞▚◣◥";

let busy = false;
let humanity = 100;

// ---------- helpers ----------
function addMsg(cls, text) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

function corrupt(text) {
  // glitch intensity scales as humanity drops
  const p = humanity >= 75 ? 0 : humanity >= 50 ? 0.01 : humanity >= 25 ? 0.045 : 0.1;
  if (!p) return text;
  return text.split("").map((c) =>
    c !== " " && Math.random() < p ? CORRUPT[(Math.random() * CORRUPT.length) | 0] : c
  ).join("");
}

function typewriter(el, text) {
  return new Promise((resolve) => {
    el.classList.add("cursor");
    let i = 0;
    const tick = () => {
      i = Math.min(text.length, i + 2 + ((Math.random() * 3) | 0));
      el.textContent = corrupt(text.slice(0, i));
      chatLog.scrollTop = chatLog.scrollHeight;
      if (i < text.length) setTimeout(tick, 18);
      else { el.textContent = corrupt(text); el.classList.remove("cursor"); resolve(); }
    };
    tick();
  });
}

// ---------- HUD ----------
function updateHUD(state) {
  humanity = state.humanity;
  $("hp-val").textContent = state.hp;
  $("hp-bar").style.width = Math.max(0, Math.min(100, state.hp * 5)) + "%";
  $("hum-val").textContent = state.humanity;
  const hb = $("hum-bar");
  hb.style.width = state.humanity + "%";
  hb.classList.toggle("low", state.humanity < 50);
  $("creds-val").textContent = "\u00A4" + state.creds;
  $("cyber-list").textContent = state.cyberware.length ? state.cyberware.join(" | ") : "\u2014 none \u2014";

  document.body.classList.remove("g0", "g1", "g2", "g3");
  document.body.classList.add(
    state.humanity >= 75 ? "g0" : state.humanity >= 50 ? "g1" : state.humanity >= 25 ? "g2" : "g3"
  );
}

// ---------- neural log ----------
async function refreshMemories(deleted = []) {
  // animate deleted memories glitching out first
  if (deleted.length) {
    for (const d of deleted) {
      const el = document.querySelector(`.mem[data-id="${d.id}"]`);
      if (el) el.classList.add("glitch-out");
      else {
        const ghost = document.createElement("div");
        ghost.className = "mem glitch-out";
        ghost.textContent = d.content;
        $("memory-list").prepend(ghost);
      }
    }
    await new Promise((r) => setTimeout(r, 1200));
  }
  try {
    const res = await fetch("/api/memories");
    const data = await res.json();
    renderMemories(data.memories || []);
  } catch (e) { /* sidebar is non-critical */ }
}

function renderMemories(mems) {
  const list = $("memory-list");
  list.innerHTML = "";
  $("mem-count").textContent = "[" + mems.length + "]";
  for (const m of mems) {
    const div = document.createElement("div");
    div.className = "mem";
    div.dataset.id = m.id;
    div.textContent = m.content;
    list.appendChild(div);
  }
  if (!mems.length) {
    const div = document.createElement("div");
    div.className = "mem";
    div.textContent = "// no engrams stored";
    list.appendChild(div);
  }
}

// ---------- d20 ----------
function showRoll(value) {
  return new Promise((resolve) => {
    const overlay = $("d20-overlay");
    const d20 = $("d20");
    const num = $("d20-num");
    const label = $("d20-label");
    d20.classList.remove("nat20", "nat1");
    overlay.classList.remove("hidden");

    let spins = 0;
    const spin = setInterval(() => {
      num.textContent = 1 + ((Math.random() * 20) | 0);
      if (++spins > 14) {
        clearInterval(spin);
        num.textContent = value;
        label.textContent =
          value === 20 ? "NATURAL 20 \u2014 LEGENDARY" :
          value === 1 ? "NATURAL 1 \u2014 CATASTROPHE" :
          value >= 15 ? "SUCCESS" :
          value >= 10 ? "SUCCESS AT A COST" : "FAILURE";
        if (value === 20 || value === 1) {
          d20.classList.add(value === 20 ? "nat20" : "nat1");
          const flash = $("flash");
          flash.className = value === 20 ? "gold boom" : "blood boom";
          setTimeout(() => (flash.className = ""), 600);
          document.body.classList.add("shake");
          setTimeout(() => document.body.classList.remove("shake"), 500);
        }
        setTimeout(() => { overlay.classList.add("hidden"); resolve(); }, 1200);
      }
    }, 70);
  });
}

// ---------- core turn ----------
async function sendMessage(text, hidden = false) {
  if (busy) return;
  busy = true;
  $("send-btn").disabled = true;
  if (!hidden) addMsg("user", text);
  const pending = addMsg("sys", "\u2026 uplink \u2026");

  try {
    const res = await fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, mode: $("mode").value }),
    });
    const data = await res.json();
    pending.remove();

    if (data.error) {
      addMsg("sys", "!! " + data.error);
      return;
    }

    for (const roll of data.rolls || []) await showRoll(roll);

    updateHUD(data.state);
    const gmEl = addMsg("gm", "");
    await typewriter(gmEl, data.text);
    await refreshMemories(data.deleted_memories || []);

    if ((data.deleted_memories || []).length) {
      addMsg("sys", "!! NEURAL DEGRADATION: " + data.deleted_memories.length + " engram(s) lost");
    }
    if (data.game_over) $("gameover").classList.remove("hidden");
  } catch (e) {
    pending.remove();
    addMsg("sys", "!! uplink failed: " + e.message);
  } finally {
    busy = false;
    $("send-btn").disabled = false;
    input.focus();
  }
}

// ---------- boot ----------
async function boot(recapOnly = false) {
  try {
    const res = await fetch("/api/state");
    const data = await res.json();
    updateHUD(data.state);
    renderMemories(data.memories || []);
    if (data.game_over) { $("gameover").classList.remove("hidden"); return; }
    if (data.has_memories || recapOnly) {
      addMsg("sys", "// resuming neural link \u2014 engrams detected");
      sendMessage(
        "A new session begins. Open with a short 'Previously in Neo-Hamilton...' recap of what you remember about me from your memories, then set the scene and give me a choice.",
        true
      );
    } else {
      addMsg("sys", "// first neural link \u2014 no engrams");
      sendMessage(
        "I am a new runner jacking in for the first time. Introduce the world of Neo-Hamilton 2077, my goal, and set the opening scene. End with a clear choice.",
        true
      );
    }
  } catch (e) {
    addMsg("sys", "!! backend offline: " + e.message);
  }
}

// ---------- events ----------
$("input-row").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  input.value = "";
  sendMessage(text);
});

$("new-session").addEventListener("click", async () => {
  if (busy) return;
  await fetch("/api/new_session", { method: "POST" });
  chatLog.innerHTML = "";
  addMsg("sys", "// SESSION TERMINATED \u2014 new thread spun up, same NEXUS");
  boot(true);
});

$("restart-btn").addEventListener("click", async () => {
  await fetch("/api/reset", { method: "POST" });
  $("gameover").classList.add("hidden");
  chatLog.innerHTML = "";
  boot(true);
});

boot();
