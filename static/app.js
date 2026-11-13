// NEXUS frontend — plain JS, no build step
const $ = (id) => document.getElementById(id);
const chatLog = $("chat-log");
const input = $("chat-input");
const CORRUPT = "▓▒░█▞▚◣◥";

let busy = false;
let runOver = false; // input stays locked until PLAY AGAIN
let humanity = 100;
let lastData = {}; // latest /api/state or turn response
const JSON_H = { "Content-Type": "application/json" };
const postJSON = (url, body = {}) => fetch(url, { method: "POST", headers: JSON_H, body: JSON.stringify(body) });

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

let skipTyping = false; // set by click/Enter mid-page to finish it instantly
function typewriter(el, text, glitchy = true) {
  const fx = glitchy ? corrupt : (t) => t;
  return new Promise((resolve) => {
    el.classList.add("cursor");
    const start = performance.now();
    const CPS = 90; // chars per second; time-based so background-tab throttling can't stall it
    const tick = () => {
      const i = skipTyping ? text.length : Math.min(text.length, Math.floor(((performance.now() - start) / 1000) * CPS));
      el.textContent = fx(text.slice(0, i));
      if (i < text.length) setTimeout(tick, 18);
      else { el.textContent = fx(text); el.classList.remove("cursor"); resolve(); }
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
  const cls = (lastData.classes || {})[state.class];
  $("who-val").textContent = state.handle ? state.handle + (cls ? " // " + cls.name : "") : "";
  const inv = state.inventory || [];
  $("kit-list").textContent = inv.length ? inv.map((i) => i.name).join(" | ") : "\u2014 empty \u2014";
  $("kit-list").title = inv.map((i) => `${i.name}: ${i.description}`).join("\n");
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
    showTab("memory"); // let the player watch the engrams die
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
  if (deleted.length) setTimeout(() => showTab("chat"), 900);
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
// rec: the server's turn rating {power, d20, tier, ...}
function showRoll(rec) {
  const value = rec.d20;
  return new Promise((resolve) => {
    const overlay = $("d20-overlay");
    const d20 = $("d20");
    const num = $("d20-num");
    const label = $("d20-label");
    const hint = $("d20-hint");
    d20.classList.remove("nat20", "nat1");
    label.textContent = "";
    hint.classList.add("hidden");
    overlay.classList.remove("hidden");

    const start = performance.now();
    const spin = setInterval(() => {
      num.textContent = 1 + ((Math.random() * 20) | 0);
      if (performance.now() - start > 1000) {
        clearInterval(spin);
        num.textContent = value;
        label.textContent = `Prompt power ${rec.power}, d20 ${value}, result: ${rec.tier}` +
          (value === 20 ? " (NAT 20)" : value === 1 ? " (NAT 1)" : "");
        if (value === 20 || value === 1) {
          d20.classList.add(value === 20 ? "nat20" : "nat1");
          const flash = $("flash");
          flash.className = value === 20 ? "gold boom" : "blood boom";
          setTimeout(() => (flash.className = ""), 600);
          document.body.classList.add("shake");
          setTimeout(() => document.body.classList.remove("shake"), 500);
        }
        // wait for click / Enter / Space; ignore the first 400ms so a double-click can't skip the result
        hint.classList.remove("hidden");
        const landedAt = performance.now();
        rollContinue = () => {
          if (performance.now() - landedAt < 400) return;
          rollContinue = null;
          hint.classList.add("hidden");
          overlay.classList.add("hidden");
          resolve();
        };
      }
    }, 70);
  });
}

// ---------- scene: top-down 8-bit, fillRect only ----------
const ctx = $("scene").getContext("2d");
ctx.setTransform(2, 0, 0, 2, 0, 0); // art is 240x135 "pixels" on the 480x270 canvas
const W = 240, H = 135;
const TEAL = "#00B4C8", YEL = "#FFD23F", PINK = "#FF3B5C";
const INK = "#07080d", WALL = "#262b40", HI = "#3b425e", STEEL = "#4b5470";

function R(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); }
function floor(a, b, y0 = 0, y1 = H, s = 8) {
  for (let y = y0; y < y1; y += s)
    for (let x = 0; x < W; x += s) R(x, y, s, Math.min(s, y1 - y), ((x + y - y0) / s) % 2 ? a : b);
}
function backWall(h, c, trim) { R(0, 0, W, h, c); R(0, h, W, 2, trim); }
function neon(x, y, w, h, c) { R(x - 2, y - 2, w + 4, h + 4, c + "33"); R(x, y, w, h, c); }
function crate(x, y) {
  R(x, y, 14, 12, "#4a3019"); R(x + 1, y + 1, 12, 10, "#7a5230");
  R(x + 1, y + 5, 12, 1, "#4a3019"); R(x + 6, y + 1, 1, 10, "#4a3019");
}
function terminal(x, y) {
  R(x, y, 20, 14, STEEL); R(x + 2, y + 2, 16, 9, INK); R(x + 7, y + 14, 6, 3, STEEL);
  R(x + 3, y + 3, 10, 1, TEAL); R(x + 3, y + 5, 6, 1, TEAL); R(x + 3, y + 7, 12, 1, TEAL);
}

const SCENES = {
  alley() {
    floor("#13151d", "#171a24");
    R(0, 0, 56, H, WALL); R(184, 0, 56, H, WALL);                                   // side walls
    for (let y = 3; y < H; y += 7) { R(0, y, 54, 1, "#1d2133"); R(186, y, 54, 1, "#1d2133"); }
    R(54, 0, 2, H, INK); R(184, 0, 2, H, INK);
    R(56, 0, 128, 28, "#1d2133"); R(56, 28, 128, 2, HI);                            // back wall
    neon(76, 8, 36, 7, PINK); neon(150, 5, 5, 18, TEAL);                             // signs
    R(62, 34, 30, 16, "#2c4636"); R(62, 34, 30, 3, "#3f6a50");                       // dumpster
    crate(152, 40); crate(160, 52); crate(66, 96);
    R(88, 116, 26, 4, TEAL + "40"); R(140, 74, 18, 3, PINK + "40");                 // puddles
  },
  street() {
    backWall(34, WALL, HI);
    for (let x = 6; x < W; x += 40) { R(x, 10, 26, 20, INK); R(x + 2, 12, 22, 16, TEAL + "30"); }
    neon(10, 3, 46, 4, YEL); neon(128, 3, 60, 4, PINK);
    floor("#2e3246", "#2a2e41", 36, 106);                                            // sidewalk
    R(0, 106, W, 2, HI); R(0, 108, W, 27, "#14161e");                               // curb + road
    for (let x = 4; x < W; x += 24) R(x, 120, 12, 2, YEL);
    R(168, 111, 44, 18, PINK); R(173, 113, 34, 7, INK); R(171, 128, 7, 3, INK); R(203, 128, 7, 3, INK);
    R(40, 40, 3, 52, STEEL); R(34, 38, 15, 3, STEEL); R(36, 41, 11, 2, YEL);         // street lamp
    R(28, 92, 28, 6, YEL + "22");
    crate(196, 70); crate(204, 82);
  },
  corp_lobby() {
    backWall(40, "#18263a", TEAL);
    for (let i = 0; i < 12; i++) R(119 - i, 6 + i * 2, i * 2 + 2, 2, TEAL);          // Delta Corp logo
    for (let i = 0; i < 6; i++) R(119 - i, 14 + i * 2, i * 2 + 2, 2, "#18263a");
    neon(40, 14, 40, 3, YEL); neon(160, 14, 40, 3, YEL);
    floor("#c9d2de", "#b9c3d1", 42);                                                 // polished tiles
    R(76, 54, 88, 14, "#2a3346"); R(76, 54, 88, 2, TEAL); R(76, 68, 88, 2, "#0d1018"); // reception desk
    terminal(110, 40);
    for (const x of [14, 214]) { R(x + 1, 56, 10, 8, "#8a5a32"); R(x, 46, 12, 10, "#2f6b3f"); R(x + 2, 42, 8, 6, "#3e8a52"); }
    R(0, 118, 92, 17, TEAL + "30"); R(148, 118, 92, 17, TEAL + "30"); R(92, 118, 56, 1, TEAL); // glass doors
    R(196, 80, 10, 14, INK); R(198, 76, 6, 5, STEEL); R(199, 78, 4, 1, PINK);         // security drone
  },
  clinic() {
    backWall(36, "#1b2a2c", HI);
    neon(18, 8, 4, 16, PINK); neon(12, 14, 16, 4, PINK);                             // med cross
    R(56, 6, 44, 24, INK); R(58, 8, 40, 20, "#0c3036");                              // vitals monitor
    for (let x = 60; x < 96; x += 2) R(x, x % 10 ? 18 : 12, 2, 1, TEAL);
    R(150, 6, 30, 22, STEEL); R(152, 8, 26, 18, "#1b2226"); R(156, 14, 18, 4, "#9aa6b8"); R(172, 12, 4, 8, "#9aa6b8"); // cyberarm
    floor("#243436", "#203032", 38);
    R(20, 42, 26, 30, "#334450"); R(22, 44, 22, 12, INK);                            // cabinet
    for (let x = 24; x < 42; x += 5) R(x, 47, 3, 7, x % 2 ? TEAL : YEL);
    R(148, 48, 54, 24, STEEL); R(150, 50, 50, 20, "#e2eef2"); R(150, 50, 12, 20, "#a9cbd6"); // bed
    R(150, 72, 3, 4, INK); R(197, 72, 3, 4, INK);
    R(208, 40, 2, 34, STEEL); R(204, 40, 10, 2, STEEL); R(205, 43, 8, 9, TEAL + "99"); // IV drip
    R(60, 96, 30, 14, STEEL); R(62, 98, 26, 10, "#5f6b85"); R(64, 100, 6, 2, PINK);   // surgical tray
    R(162, 92, 4, 2, PINK); R(170, 98, 2, 2, PINK);
  },
  rooftop() {
    R(0, 0, W, H, "#07080f");
    floor("#373c50", "#33384a", 0, 108);                                             // roof deck
    R(0, 108, W, 3, HI); R(0, 111, W, 2, INK);                                      // the edge
    for (let x = 3; x < W; x += 7) R(x, 117 + ((x * 13) % 14), 2, 2, [TEAL, YEL, PINK][x % 3]); // city below
    R(16, 14, 34, 22, STEEL); R(18, 16, 30, 18, "#39405a");                         // AC unit
    for (let y = 18; y < 33; y += 3) R(20, y, 26, 1, "#262b3c");
    R(190, 6, 36, 34, "#5a4636"); R(190, 6, 36, 4, "#6e5644"); R(194, 40, 3, 8, STEEL); R(219, 40, 3, 8, STEEL);
    neon(72, 4, 94, 12, PINK); R(74, 6, 90, 8, INK); R(78, 9, 34, 2, YEL); R(116, 9, 44, 2, TEAL); // billboard
    R(76, 16, 2, 14, STEEL); R(160, 16, 2, 14, STEEL);
    R(140, 60, 8, 22, STEEL); R(143, 52, 2, 8, STEEL); R(142, 50, 4, 2, PINK);       // antenna
    crate(40, 78); crate(48, 90);
  },
  server_room() {
    backWall(30, "#0c111e", TEAL);
    for (let x = 8; x < W; x += 16) R(x, 8, 8, 2, x % 32 ? TEAL + "66" : PINK + "66");
    floor("#0f131d", "#0c1018", 32);
    for (let x = 0; x < W; x += 16) R(x, 32, 1, H - 32, TEAL + "14");               // floor grid
    for (let x = 8; x < W; x += 34) {
      if (x > 92 && x < 140) continue;                                               // keep the aisle clear
      R(x, 36, 24, 44, "#192136"); R(x, 36, 24, 3, HI); R(x, 80, 24, 2, INK);
      for (let y = 42; y < 78; y += 4) { R(x + 3, y, 2, 1, (x + y) % 3 ? TEAL : YEL); R(x + 7, y, 14, 1, INK); }
    }
    terminal(110, 44); R(113, 47, 4, 1, PINK); R(119, 61, 2, 20, "#2a2f45");         // root terminal + cable
    R(0, 114, W, 1, PINK + "88"); R(0, 118, W, 1, TEAL + "88");
  },
};

// player: 12x16 sprite (trench coat, teal visor)
const PLAYER = [
  "....hhhh....", "...hhhhhh...", "...vvvvvv...", "...ssssss...",
  "....ssss....", "..cCCCCCCc..", ".CCCCiiCCCC.", ".CCCCiiCCCC.",
  ".CCCCiiCCCC.", "sCCCbYYbCCCs", ".CCCCCCCCCC.", ".CCCC..CCCC.",
  ".CCCl..lCCC.", "..CCl..lCC..", "...ll..ll...", "...kk..kk...",
];
const PAL = { h: "#1b1b2b", v: TEAL, s: "#d9a07a", c: "#3a271b", C: "#5c4030", i: PINK, b: "#2a1d14", Y: YEL, l: "#22263a", k: INK };
const PX = 114, PY = 88;
let sceneLoc = null, bobUp = 0;

function renderScene() {
  ctx.clearRect(0, 0, W, H);
  (SCENES[sceneLoc] || SCENES.street)();
  R(PX + 1, PY + 15, 10, 2, "#00000088"); // shadow
  PLAYER.forEach((row, j) => [...row].forEach((p, i) => PAL[p] && R(PX + i, PY - bobUp + j, 1, 1, PAL[p])));
}

function drawScene(location) {
  const loc = SCENES[location] ? location : "street";
  if (sceneLoc && loc !== sceneLoc) {
    const w = $("wipe");
    w.classList.remove("run"); void w.offsetWidth; w.classList.add("run");
  }
  sceneLoc = loc;
  $("scene-label").textContent = "LOC // " + loc.replace("_", " ").toUpperCase();
  renderScene();
}

setInterval(() => { bobUp ^= 1; renderScene(); }, 500); // 2-frame idle bob
drawScene("street");

// ---------- phone tabs ----------
function showTab(name) {
  document.querySelectorAll(".phone-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll("#phone .tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
}
document.querySelectorAll(".phone-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------- dialogue box (queue of NEXUS + SYSTEM lines) ----------
const dlg = $("dialogue"), dlgText = $("dlg-text"), dlgLog = $("dlg-log");
let dlgTyping = false, dlgAdvance = null, pageDoneAt = 0, rollContinue = null;
const dlgHistory = []; // last 20 shown pages, for the LOG panel
const sys = (text) => ({ who: "sys", text });
const STAT_NAMES = { hp: "HP", humanity: "Humanity", creds: "Creds" };

// pages of <= max chars, breaking on sentences, then words; short lines/paragraphs share a page
function paginate(text, max = 110) {
  const pages = [];
  let cur = "";
  const flush = () => { if (cur.trim()) pages.push(cur.trim()); cur = ""; };
  for (const para of String(text).split(/\n+/)) {
    (para.match(/[^.!?]*[.!?]+["')\]]*|[^.!?]+$/g) || []).forEach((raw, si) => {
      const s = raw.trim();
      if (!s) return;
      const sep = cur ? (si === 0 ? "\n" : /^\s/.test(raw) ? " " : "") : "";
      if ((cur + sep + s).length <= max) { cur += sep + s; return; }
      flush();
      for (const w of s.split(/\s+/)) {
        if (cur && (cur + " " + w).length > max) flush();
        cur += (cur ? " " : "") + w;
      }
    });
  }
  flush();
  return pages.length ? pages : [""];
}

function setSpeaker(who) {
  dlg.classList.toggle("sys", who === "sys");
  $("dlg-name").textContent = who === "sys" ? "SYSTEM" : "NEXUS";
}

// corner marker: "more" = blinking ▼, "done" = "...", "pending" = uplink, null = none
function setMark(mark) {
  dlg.classList.remove("more", "done", "pending");
  if (mark) dlg.classList.add(mark);
}

// the previous turn's last page stays up while we wait; a note (new session etc.) replaces it
function showPending(note) {
  if (note) { setSpeaker("sys"); dlgText.textContent = note; dlg.classList.remove("hidden"); }
  setMark("pending");
}

async function playDialogue(lines, holdLast = false) {
  if (document.activeElement) document.activeElement.blur(); // so Space can't re-click a focused button
  for (const [li, line] of lines.entries()) {
    if (line.before) await line.before();
    setSpeaker(line.who);
    dlg.classList.remove("hidden");
    const pages = paginate(line.text);
    for (const [pi, page] of pages.entries()) {
      setMark(null);
      dlgHistory.push({ who: line.who, text: page });
      if (dlgHistory.length > 20) dlgHistory.shift();
      skipTyping = false; dlgTyping = true;
      await typewriter(dlgText, page, line.who === "gm");
      dlgTyping = false; pageDoneAt = performance.now();
      const last = li === lines.length - 1 && pi === pages.length - 1;
      if (last && !holdLast) { setMark("done"); return; } // last page stays up
      setMark("more");
      await new Promise((r) => (dlgAdvance = r));
      dlgAdvance = null;
      if (last) setMark("done"); // run over: click past the final lines, then results
    }
  }
}

// mid-typing: finish the page only. After a page lands: ignore advances for 250ms.
function advanceDialogue() {
  if (dlgTyping) skipTyping = true;
  else if (dlgAdvance && performance.now() - pageDoneAt >= 250) dlgAdvance();
}
dlg.addEventListener("click", advanceDialogue);
$("d20-overlay").addEventListener("click", () => rollContinue && rollContinue());
document.addEventListener("keydown", (e) => {
  if (!dlgLog.classList.contains("hidden")) { if (e.key === "Escape") dlgLog.classList.add("hidden"); return; }
  if (e.key !== "Enter" && e.key !== " ") return;
  if (rollContinue) { e.preventDefault(); if (!e.repeat) rollContinue(); }
  else if (dlgTyping || dlgAdvance) { e.preventDefault(); if (!e.repeat) advanceDialogue(); }
});

// LOG: safety net for anything clicked past
$("dlg-log-btn").addEventListener("click", (e) => {
  e.stopPropagation(); // don't advance the dialogue
  e.currentTarget.blur();
  const list = $("dlg-log-list");
  list.innerHTML = "";
  for (const h of dlgHistory) {
    const row = document.createElement("div");
    row.className = "log-line" + (h.who === "sys" ? " sys" : "");
    const who = document.createElement("b");
    who.textContent = h.who === "sys" ? "SYSTEM" : "NEXUS";
    row.append(who, h.text);
    list.appendChild(row);
  }
  if (!dlgHistory.length) list.textContent = "// buffer empty";
  dlgLog.classList.remove("hidden");
  list.scrollTop = list.scrollHeight;
});
$("dlg-log-close").addEventListener("click", () => dlgLog.classList.add("hidden"));

const signed = (n) => (n > 0 ? "+" : "") + n;
function scoreLine(r) {
  const parts = r.breakdown.filter((b) => b.delta).map((b) => `${signed(b.delta)} ${b.label}`);
  if (r.combo_bonus) parts.push(`+${r.combo_bonus} combo x${r.combo}`);
  return `Prompt power ${r.power}, d20 ${r.d20}: ${r.tier}. Turn score ${r.turn_score} (${r.turn_grade})` +
    (parts.length ? "\n" + parts.join(", ") : "");
}

// ---------- screen fx: canvas + CSS only, each under 900ms ----------
const fxCanvas = $("fx"), fxc = fxCanvas.getContext("2d");
let fxRun = 0;
function fxStar(x, y, s) { // 4-point star
  fxc.beginPath(); fxc.moveTo(x, y - s);
  fxc.quadraticCurveTo(x, y, x + s, y); fxc.quadraticCurveTo(x, y, x, y + s);
  fxc.quadraticCurveTo(x, y, x - s, y); fxc.quadraticCurveTo(x, y, x, y - s);
  fxc.fill();
}
function playEffect(type) {
  const body = document.body;
  if (type === "glitch") {
    body.classList.remove("fx-glitch"); void body.offsetWidth; body.classList.add("fx-glitch");
    setTimeout(() => body.classList.remove("fx-glitch"), 300);
    return;
  }
  if (type === "explosion" || type === "hit") { body.classList.add("shake"); setTimeout(() => body.classList.remove("shake"), 500); }
  const W = (fxCanvas.width = innerWidth), H = (fxCanvas.height = innerHeight);
  const sr = $("scene").getBoundingClientRect(), cx = sr.left + sr.width / 2, cy = sr.top + sr.height / 2;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const parts = type === "explosion"
    ? Array.from({ length: 30 }, (_, i) => ({ x: cx, y: cy, vx: rnd(-14, 14), vy: rnd(-16, 8), s: rnd(4, 10), c: ["#ff8a1f", "#FFD23F", "#FF3B5C"][i % 3] }))
    : type === "sparkle"
    ? Array.from({ length: 40 }, (_, i) => ({ x: rnd(sr.left, sr.right), y: rnd(sr.top + sr.height * 0.3, sr.bottom), vy: rnd(1, 3), s: rnd(3, 7), c: i % 2 ? "#FFD23F" : "#00B4C8" }))
    : [];
  const dur = { explosion: 800, sparkle: 850, hit: 450 }[type] || 500;
  const id = ++fxRun, t0 = performance.now();
  const frame = (now) => {
    if (id !== fxRun) return; // a newer effect took over
    const t = Math.min(1, (now - t0) / dur);
    fxc.clearRect(0, 0, W, H);
    fxc.save();
    fxc.beginPath(); fxc.rect(0, 0, W, H); // cut the dialogue box out so it stays on top
    if (!dlg.classList.contains("hidden")) { const r = dlg.getBoundingClientRect(); fxc.rect(r.left - 6, r.top - 6, r.width + 12, r.height + 12); }
    fxc.clip("evenodd");
    if (type === "explosion") {
      if (t < 0.3) { fxc.fillStyle = `rgba(255,255,255,${0.8 * (1 - t / 0.3)})`; fxc.fillRect(0, 0, W, H); }
      fxc.globalAlpha = 1 - t;
      ["#ff8a1f", "#FFD23F", "#FF3B5C"].forEach((c, i) => {
        fxc.strokeStyle = c; fxc.lineWidth = 12 - i * 3;
        fxc.beginPath(); fxc.arc(cx, cy, Math.max(0, t - i * 0.1) * sr.width * 0.6, 0, Math.PI * 2); fxc.stroke();
      });
      for (const p of parts) { p.x += p.vx; p.y += p.vy; p.vy += 0.5; fxc.fillStyle = p.c; fxc.fillRect(p.x, p.y, p.s, p.s); }
    } else if (type === "sparkle") {
      fxc.globalAlpha = 1 - t; fxc.shadowColor = "#ffbf1f"; fxc.shadowBlur = 14;
      for (const p of parts) { p.y -= p.vy; fxc.fillStyle = p.c; fxStar(p.x, p.y, p.s); }
    } else if (type === "hit") {
      const g = fxc.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.2, W / 2, H / 2, Math.max(W, H) * 0.7);
      g.addColorStop(0, "rgba(255,0,40,0)"); g.addColorStop(1, `rgba(255,0,40,${0.7 * (1 - t)})`);
      fxc.fillStyle = g; fxc.fillRect(0, 0, W, H);
    }
    fxc.restore();
    if (t < 1) requestAnimationFrame(frame); else fxc.clearRect(0, 0, W, H);
  };
  requestAnimationFrame(frame);
}

// strongest one only: explosion > sparkle > glitch > hit, driven by the server's outcome tier
function pickEffect(d) {
  const r = d.turn_rating || {}, ch = d.stat_changes || [];
  const hpLoss = -ch.filter((c) => c.stat === "hp" && c.amount < 0).reduce((a, c) => a + c.amount, 0);
  if (hpLoss >= 5 || r.tier === "DISASTER" || r.d20 === 1) return "explosion";
  if (r.tier === "CRITICAL SUCCESS" || r.d20 === 20 || ["S", "A"].includes(r.turn_grade)) return "sparkle";
  if ((d.installed || []).length) return "glitch";
  if (hpLoss > 0 || r.tier === "FAIL") return "hit";
  return null;
}

// ---------- mission banner ----------
function updateMission(d) {
  const m = d.mission || {}, max = d.max_turns || 5, left = Math.max(0, max - (d.turn || 0));
  $("mission-title").textContent = m.title || "awaiting briefing";
  $("mission-obj").textContent = m.objective ? "— " + m.objective : "";
  $("mission-obj").title = m.objective || "";
  $("turns-left").textContent = `PROMPTS LEFT: ${left}/${max}`;
  $("turns-left").classList.toggle("low", !!m.objective && left <= 2);
}

// ---------- this turn's objective (set by the GM via set_objective) ----------
function updateBeat(d) {
  const b = d.beat;
  $("beat").classList.toggle("hidden", !b || !b.text || runOver);
  if (!b) return;
  $("beat-text").textContent = b.text;
  $("beat-con").textContent = b.constraint ? " // " + b.constraint.toUpperCase() : "";
}

// ---------- black market: 6 daily items, bought with earned creds ----------
function renderMarket(m) {
  $("mk-creds").textContent = "\u00A4" + m.creds;
  $("mk-count").textContent = `${m.inventory.length}/${m.max_items}`;
  const box = $("mk-items");
  box.textContent = "";
  for (const it of m.items) {
    const card = document.createElement("div");
    card.className = "mk-item" + (it.owned ? " owned" : "");
    const n = document.createElement("b"); n.textContent = it.name;
    const dsc = document.createElement("span"); dsc.textContent = it.description;
    const btn = document.createElement("button");
    btn.textContent = it.owned ? "OWNED" : `BUY \u00A4${it.price}`;
    btn.disabled = it.owned || it.price > m.creds || m.inventory.length >= m.max_items;
    btn.addEventListener("click", async () => {
      $("mk-error").textContent = "";
      const res = await postJSON("/api/market/buy", { name: it.name });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { $("mk-error").textContent = d.error || "The deal fell through."; return; }
      renderMarket(d);
      if (lastData.state) updateHUD({ ...lastData.state, creds: d.creds, inventory: d.inventory });
    });
    card.append(n, dsc, btn);
    box.appendChild(card);
  }
  $("mk-kit").textContent = "YOUR KIT: " + (m.inventory.map((i) => i.name).join(", ") || "empty");
}

async function showMarket(note = "") {
  const m = await (await fetch("/api/market")).json();
  renderMarket(m);
  $("gameover").classList.add("hidden");
  $("market").classList.remove("hidden");
  $("mk-go").onclick = async () => {
    $("mk-go").disabled = true;
    const r = await fetch("/api/run/start", { method: "POST" });
    $("mk-go").disabled = false;
    if (!r.ok) { $("mk-error").textContent = (await r.json().catch(() => ({}))).error || "Could not start a run."; return; }
    $("market").classList.add("hidden");
    chatLog.innerHTML = "";
    boot(note);
  };
}

// ---------- results (replaces the old game-over screen) ----------
function showResults(d) {
  const r = d.rating || {}, s = d.state || {}, grade = r.grade || "D";
  const head = r.reason === "flatlined" ? "FLATLINED" : r.reason === "cyberpsychosis" ? "CYBERPSYCHOSIS"
    : ["S", "A", "B"].includes(grade) ? "MISSION COMPLETE" : "TIME OUT";
  const title = document.querySelector("#gameover .go-title");
  title.textContent = title.dataset.text = head;
  title.classList.toggle("win", head === "MISSION COMPLETE");
  $("go-mission").textContent = d.mission && d.mission.title ? "MISSION: " + d.mission.title.toUpperCase() : "";
  const g = $("go-grade");
  g.textContent = g.dataset.text = grade;
  g.className = "go-grade grade-" + grade + (grade === "D" || grade === "F" ? " glitch" : "");
  $("go-score").textContent = `${r.score ?? 0}/${r.max || 600}`;
  $("go-verdict").textContent = r.verdict || "";
  const turnRows = (r.turns || []).map((t) => `#${t.n} ${t.tier.padEnd(16)} ${String(t.turn_score).padStart(3)}  "${t.quote}"`);
  $("go-stats").textContent =
    (turnRows.length ? turnRows.join("\n") + `\nMISSION ${(r.outcome || "fail").toUpperCase()} +${r.mission_bonus || 0}  //  +\u00A4${r.creds_earned || 0}\n\n` : "") +
    `TURNS USED: ${d.turn}/${d.max_turns}\n` +
    `HP ${s.hp}  //  HUMANITY ${s.humanity}  //  CREDS ¤${s.creds}\n` +
    `CYBERWARE: ${(s.cyberware || []).join(", ") || "none"}`;
  $("restart-btn").textContent = d.can_play ? "PLAY AGAIN" : "CONTINUE";
  $("gameover").classList.remove("hidden");
}

// ---------- come back tomorrow ----------
let cbTimer = null;
function showComeback(d) {
  const end = Date.now() + (d.next_reset_seconds || 0) * 1000;
  const tick = () => {
    const s = Math.max(0, Math.round((end - Date.now()) / 1000));
    $("cb-timer").textContent = [s / 3600, (s % 3600) / 60, s % 60].map((v) => String(Math.floor(v)).padStart(2, "0")).join(":");
    if (s <= 0) { clearInterval(cbTimer); location.reload(); }
  };
  clearInterval(cbTimer); tick(); cbTimer = setInterval(tick, 1000);
  const r = d.rating;
  $("cb-today").textContent = r && d.mission && d.mission.title
    ? `TODAY: ${d.mission.title}\nGRADE ${r.grade}  //  SCORE ${r.score}  //  +\u00A4${r.creds_earned || 0}` : "";
  $("cb-streak").textContent = `STREAK: ${d.streak || 0} DAY${d.streak === 1 ? "" : "S"}`;
  $("gameover").classList.add("hidden");
  $("comeback").classList.remove("hidden");
}

// ---------- first visit: 18+ notice, handle, class ----------
function showOnboard(classes) {
  const box = $("ob-classes");
  box.textContent = "";
  let pick = "";
  for (const [id, c] of Object.entries(classes || {})) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ob-class";
    const n = document.createElement("b"); n.textContent = c.name;
    const s = document.createElement("span"); s.textContent = c.blurb;
    const t = document.createElement("small");
    t.textContent = Object.entries(c.stats).map(([k, v]) => `${k.toUpperCase()} ${v}`).join("  ");
    b.append(n, s, t);
    b.addEventListener("click", () => { pick = id; box.querySelectorAll(".ob-class").forEach((x) => x.classList.toggle("sel", x === b)); });
    box.appendChild(b);
  }
  $("ob-go").onclick = async () => {
    $("ob-error").textContent = "";
    const res = await postJSON("/api/profile", { handle: $("ob-handle").value, cls: pick, adult: $("ob-adult").checked });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { $("ob-error").textContent = d.error || "Could not create your runner."; return; }
    $("onboard").classList.add("hidden");
    boot();
  };
  $("onboard").classList.remove("hidden");
  $("ob-handle").focus();
}

// ---------- core turn ----------
function setBusy(on) {
  busy = on;
  const lock = on || runOver;
  $("send-btn").disabled = lock;
  input.disabled = lock;
  input.placeholder = runOver ? "" : "what will you do next?";
  if (!lock && $("onboard").classList.contains("hidden")) input.focus();
}

const clean = (t) => String(t).replace(/\*\*|__/g, ""); // drop markdown bold

// kind: "turn" (typed, costs a prompt) | "open" / "resume" (hidden; the server writes the text)
async function sendMessage(text, kind = "turn", note = "") {
  if (busy) return;
  const hidden = kind !== "turn";
  setBusy(true);
  if (!hidden) addMsg("user", text);
  showPending(note);

  try {
    const res = await postJSON("/api/message", { message: hidden ? "" : text, mode: $("mode").value, kind });
    if (res.status === 409) {
      const st = await (await fetch("/api/state")).json();
      lastData = st;
      if (st.run_over) { runOver = true; updateHUD(st.state); updateMission(st); showResults(st); }
      else setTimeout(() => boot(), 0);
      return;
    }
    const data = await res.json();

    if (data.error) {
      if (!hidden && res.status !== 500) input.value = text; // rate limit / length: let them retry
      await playDialogue([sys("!! " + data.error)]);
      return;
    }
    lastData = { ...lastData, ...data };

    const fxType = pickEffect(data); // fire before any dialogue starts
    if (fxType) playEffect(fxType);

    // queue: rolls (overlay first), stat changes, implants, lost engrams, then NEXUS
    const tr = data.turn_rating, changes = data.stat_changes || [];
    const installed = data.installed || [], deleted = data.deleted_memories || [];
    const lines = tr ? [{ ...sys(scoreLine(tr)), before: () => showRoll(tr) }] : [];
    const humDelta = changes.filter((c) => c.stat === "humanity").reduce((a, c) => a + c.amount, 0);
    const implantCost = humanity + humDelta - data.state.humanity; // read before updateHUD
    for (const c of changes) lines.push(sys(`${STAT_NAMES[c.stat] || c.stat} ${c.amount > 0 ? "+" : ""}${c.amount}`));
    for (const it of data.items_gained || []) lines.push(sys(`+ ITEM: ${it.name}`));
    for (const n of data.items_used || []) lines.push(sys(`- USED: ${n}`));
    for (const n of data.items_denied || []) lines.push(sys(`!! NOT IN YOUR KIT: ${n}`));
    for (const c of data.npc_changes || []) {
      lines.push(sys(c.event === "met" ? `CONTACT: ${c.name}, ${c.role}`
        : `${c.name} ${signed(c.delta)} (now ${signed(c.relationship)}): ${c.note}`));
    }
    for (const name of installed) {
      lines.push(sys(`Installed: ${name}.` + (installed.length === 1 && implantCost > 0 ? ` Humanity -${implantCost}` : "")));
    }
    let memP = null;
    deleted.forEach((d, i) => lines.push({
      ...sys("!! NEURAL DEGRADATION: 1 engram lost"),
      before: i ? null : () => { memP = refreshMemories(deleted); },
    }));
    lines.push({ who: "gm", text: clean(data.text) });

    // reveal HUD + scene right after the dice
    const first = lines[tr ? 1 : 0], firstBefore = first.before;
    first.before = async () => {
      updateHUD(data.state);
      updateMission(data);
      updateBeat(data);
      drawScene(data.location);
      if (firstBefore) await firstBefore();
    };

    await playDialogue(lines, !!data.run_over);
    await (memP || refreshMemories());
    if (data.run_over) { runOver = true; updateBeat(data); showResults(data); }
  } catch (e) {
    await playDialogue([sys("!! uplink failed: " + e.message)]);
  } finally {
    setBusy(false);
  }
}

// ---------- boot ----------
// hidden boot messages never cost a prompt (counts_turn: false)
async function boot(note = "") {
  try {
    const data = await (await fetch("/api/state")).json();
    lastData = data;
    if (data.needs_profile) { showOnboard(data.classes); return; }
    updateHUD(data.state);
    updateMission(data);
    updateBeat(data);
    drawScene(data.state.location);
    renderMemories(data.memories || []);
    if (data.run_over) { // today's run is finished
      runOver = true; setBusy(false);
      if (data.can_play) showResults(data); else showComeback(data);
      return;
    }
    if (!data.active) {
      if (!data.can_play) { runOver = true; setBusy(false); showComeback(data); return; }
      runOver = false; setBusy(false);
      return showMarket(note); // shop first, then JACK IN starts the run
    }
    runOver = false;
    if (data.opened) sendMessage("", "resume", note || "// resuming neural link \u2014 mission in progress");
    else sendMessage("", "open", note || ((data.history || []).length ? "// new day \u2014 engrams detected" : "// first neural link \u2014 no engrams"));
  } catch (e) {
    playDialogue([sys("!! backend offline: " + e.message)]);
  }
}

// ---------- events ----------
$("input-row").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  if (text.length < 3) { playDialogue([sys("!! Say a little more: at least 3 characters.")]); return; }
  input.value = "";
  sendMessage(text);
});

$("new-session").addEventListener("click", async () => {
  if (busy || runOver) return;
  await fetch("/api/new_session", { method: "POST" });
  chatLog.innerHTML = "";
  boot("// SESSION TERMINATED \u2014 new thread spun up, same NEXUS");
});

$("new-campaign").addEventListener("click", async () => {
  if (busy || !confirm("Wipe your runner? NEXUS forgets everything and your gear is gone. Today's run limit still applies.")) return;
  const res = await fetch("/api/new_campaign", { method: "POST" });
  if (!res.ok) { playDialogue([sys("!! " + ((await res.json().catch(() => ({}))).error || "wipe failed"))]); return; }
  runOver = false;
  $("gameover").classList.add("hidden");
  chatLog.innerHTML = "";
  boot("// CAMPAIGN WIPED \u2014 fresh NEXUS instance, zero engrams");
});

// PLAY AGAIN: new run, same NEXUS (memories carry over)
$("restart-btn").addEventListener("click", async () => {
  if (!lastData.can_play) { showComeback(lastData); return; }
  runOver = false;
  $("gameover").classList.add("hidden");
  showMarket("// NEW RUN \u2014 fresh body, same NEXUS");
});

boot();
