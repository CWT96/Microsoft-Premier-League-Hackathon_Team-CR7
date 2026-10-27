// Matchday Call fan app: landing -> pick a team -> live calls over WS /demo/ws.
// The server drives all timing; this file only reacts to its messages.
"use strict";

const $ = (id) => document.getElementById(id);
const TEAM_NAMES = { ARS: "Arsenal", CHE: "Chelsea" };
const POP_MS = 1600;      // points pop-up over the pitch
const GOAL_MS = 2600;     // goal banner
const JUMP_MS = 2000;     // jump card (JUMP_CARD_S on the server)
const RESULT_HOLD_MS = 5500; // result stays on the call card, then back to "reading the play"
const SWITCH_GUARD_MS = 600; // taps ignored on call options right after the auto-switch to REAL TIME
const DRIBBLE_TAG_MS = 1000; // "DRIBBLE!" label on the ball
const SILENCE_MS = 40000; // no message for this long (half time is 10 s) = connection lost
const ROW_MS = 2800;      // full-time count-up per row (design Result.dc.html)
const ZERO_ROW_MS = 700;  // a row worth 0 points lands quickly
const SIGNUP_WAIT_MS = 6000; // no signup reply by then = let the user try again
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

let team = null;          // ARS or CHE, picked on screen 2
let ws = null;            // current socket; messages from older sockets are ignored
let s = null;             // live state of the current run
const timers = {};

function later(name, ms, fn) {
  clearTimeout(timers[name]);
  timers[name] = setTimeout(fn, ms);
}

function clearTimers() {
  Object.keys(timers).forEach((k) => clearTimeout(timers[k]));
}

function show(screen) {
  ["landing", "pick", "live"].forEach((id) => { $(id).hidden = id !== screen; });
  window.scrollTo(0, 0);
}

function scoreText(score) {
  return score[0] + " – " + score[1];
}

// ............................................................ screens 1 and 2

$("go-pick").onclick = () => show("pick");
$("back").onclick = () => show("landing");
document.querySelectorAll(".team-btn").forEach((btn) => {
  btn.onclick = () => {
    team = btn.dataset.team;
    document.querySelectorAll(".team-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(b === btn));
    });
    $("pick-summary").textContent = "You're backing " + TEAM_NAMES[team] + ". Starts as soon as you tap.";
    $("start").disabled = false;
  };
});
$("start").onclick = () => { if (team) startRun(); };
$("again").onclick = $("ft-again").onclick = () => startRun();

// ............................................................ socket

function startRun() {
  if (ws) {
    const old = ws;
    ws = null;               // detach first so its close does not show "connection lost"
    try { old.close(); } catch (e) { /* already closed */ }
  }
  clearTimers();
  if (s && s.ft) cancelAnimationFrame(s.ft.raf);
  s = { score: [0, 0], total: 0, streak: 0, call: null, picks: {}, finished: false,
    tab: "live", pops: [],
    inBreak: false, htSeen: false, ht: null, ft: null, sheet: null,
    save: { saved: false, pending: false, email: "", bonus: 0, fresh: false },
    fc: { live: false, info: null, cards: {}, points: 0,
      final: { ft: null, hs: 0, aw: 0, flash: "", saved: null, locked: false, res: null } } };
  resetLiveUi();
  show("live");

  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/demo/ws";
  const sock = new WebSocket(url);
  ws = sock;
  sock.onopen = () => send({ type: "start", data: { team: team } });
  later("silence", SILENCE_MS, () => dropSocket(sock));
  sock.onmessage = (ev) => {
    if (sock !== ws) return;
    if (!s.finished) later("silence", SILENCE_MS, () => dropSocket(sock)); // after full time the server closes it
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (!msg || typeof msg.type !== "string") return;
    try { handle(msg.type, msg.data || {}); } catch (e) { console.error(msg.type, e); }
  };
  sock.onclose = (ev) => dropSocket(sock, ev.code);
}

// The socket closed, or went silent: show "connection lost" unless the match finished.
function dropSocket(sock, code) {
  if (sock !== ws) return;
  ws = null;
  try { sock.close(); } catch (e) { /* already closed */ }
  if (!s.finished) connectionLost(code);
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function handle(type, d) {
  // play resumed after half time (forecast and signup replies can arrive during the break)
  if (s.inBreak && type !== "demo.half_time" && !type.startsWith("forecast.") && !type.startsWith("signup.")) {
    s.inBreak = false;
    if (s.sheet === "half") closeSheet();
  }
  switch (type) {
    case "demo.session": onSession(d); break;
    case "demo.clock": onClock(d); break;
    case "demo.event": onEvent(d); break;
    case "demo.shape": onShape(d); break;
    case "demo.goal": onGoal(d); break;
    case "demo.jump": onJump(d); break;
    case "call.open": onCallOpen(d); break;
    case "call.locked": onCallLocked(d); break;
    case "call.result": onCallResult(d); break;
    case "demo.half_time": onBreak("half", d); break;
    case "demo.full_time": onBreak("full", d); break;
    case "forecast.locked": onForecastLocked(d); break;
    case "forecast.rejected": onForecastRejected(d); break;
    case "forecast.result": onForecastResult(d); break;
    case "forecast.final_saved": s.fc.final.saved = d; renderForecasts(); break;
    case "forecast.final_locked": onFinalLocked(d); break;
    case "forecast.final_result": onFinalResult(d); break;
    case "signup.saved": onSignupSaved(d); break;
    case "signup.rejected": onSignupRejected(d); break;
    default: break; // unknown types are ignored
  }
}

// ............................................................ live screen

function resetLiveUi() {
  $("clock").textContent = "KICK-OFF";
  $("score").textContent = "0 – 0";
  $("total").textContent = "0";
  $("backing").textContent = "Backing " + TEAM_NAMES[team];
  ["gain", "jump", "goal", "pop", "modal", "ai-caption", "sheet", "sheet-dim", "save-chip", "ft"].forEach((id) => { $(id).hidden = true; });
  $("email").value = "";
  $("done-line").textContent = "";
  setEmailError("");
  setSaveBusy(false);
  showTag(false);
  $("players").textContent = ""; // no dots left over from the previous run
  setStreak(0);
  moveBall([52.5, 34], null);
  showStandby();
  setTab("live");
  $("tab-dot").hidden = true;
  $("fc-cards").textContent = "";
  $("ft-opts").textContent = "";
  renderForecasts();
}

function onSession(d) {
  const you = d.you && TEAM_NAMES[d.you.team];
  if (you) $("backing").textContent = "Backing " + you;
  $("ai-caption").hidden = d.ai_lines !== true;
  buildForecasts(d.forecasts);
}

function onClock(d) {
  if (typeof d.clock === "string") $("clock").textContent = "LIVE " + d.clock;
  if (Array.isArray(d.score)) setScore(d.score);
  setLive(true);
}

function setScore(score) {
  s.score = score;
  $("score").textContent = scoreText(score);
  const f = s.fc.final; // the full-time pick can never be below the live score
  if (f.locked || f.res || (f.hs >= score[0] && f.aw >= score[1])) return;
  f.hs = Math.max(f.hs, score[0]);
  f.aw = Math.max(f.aw, score[1]);
  if (f.ft) {
    const ft = resultOf(f.hs, f.aw);
    setFinal(ft, ft !== f.ft ? "result" : ""); // explain a result the live score forced
  }
  else renderForecasts();
}

// Place the ball at xy, then glide to end_xy. A null keeps the ball where it is.
// A dribble flashes "DRIBBLE!" on the ball for DRIBBLE_TAG_MS, then shows the player's name.
function onEvent(d) {
  clearTimeout(timers.tag);
  moveBall(d.xy, d.end_xy);
  if (d.type === "dribble") {
    setTag("DRIBBLE!");
    if (d.player) later("tag", DRIBBLE_TAG_MS, () => setTag(d.player));
    return;
  }
  setTag(d.player);
}

function setTag(label) {
  showTag(Boolean(label));
  if (!label) return;
  const text = $("tag-text");
  text.textContent = label;
  let w = label.length * 1.8;
  try { w = text.getComputedTextLength(); } catch (e) { /* not rendered yet */ }
  placeTag(w + 2.4);
}

// Draw the 22 shirt-number dots. Dots are keyed team-num; any not in this shape are hidden.
const SVG_NS = "http://www.w3.org/2000/svg";

function onShape(d) {
  if (!Array.isArray(d.players)) return;
  const seen = new Set();
  d.players.forEach((p) => {
    if (!p || !TEAM_NAMES[p.team]) return;
    const key = p.team + "-" + p.num;
    seen.add(key);
    let g = $("pl-" + key);
    if (!g) {
      g = document.createElementNS(SVG_NS, "g");
      g.id = "pl-" + key;
      g.setAttribute("class", "pl " + p.team);
      const c = document.createElementNS(SVG_NS, "circle");
      c.setAttribute("r", "1.6");
      const t = document.createElementNS(SVG_NS, "text");
      t.textContent = String(p.num);
      g.append(c, t);
      $("players").append(g);
    }
    const x = Math.max(0, Math.min(105, Number(p.x) || 0));
    const y = Math.max(0, Math.min(68, Number(p.y) || 0));
    g.style.transform = "translate(" + x + "px, " + y + "px)";
    g.style.display = "";
  });
  Array.from($("players").children).forEach((g) => {
    if (!seen.has(g.id.slice(3))) g.style.display = "none";
  });
}

function moveBall(xy, endXy) {
  const g = $("ball");
  if (Array.isArray(xy)) {
    g.classList.add("snap");
    setBall(xy);
    void g.getBoundingClientRect(); // commit the jump before the glide starts
    g.classList.remove("snap");
  }
  if (Array.isArray(endXy)) setBall(endXy);
}

function setBall(xy) {
  const x = Math.max(1, Math.min(104, Number(xy[0]) || 0));
  const y = Math.max(1, Math.min(67, Number(xy[1]) || 0));
  $("ball").style.transform = "translate(" + x + "px, " + y + "px)";
  $("ball").dataset.x = x;
  $("ball").dataset.y = y;
}

// The label is SVG, which has no .hidden property; toggle the attribute instead.
function showTag(on) {
  if (on) $("tag").removeAttribute("hidden");
  else $("tag").setAttribute("hidden", "");
}

// Keep the name label inside the pitch: above the ball, below it near the top edge.
function placeTag(w) {
  const x = Number($("ball").dataset.x), y = Number($("ball").dataset.y);
  let left = -w / 2;
  if (x + left < 1) left = 1 - x;
  if (x + left + w > 104) left = 104 - x - w;
  const top = y < 9 ? 3 : -7.6;
  $("tag-bg").setAttribute("x", left);
  $("tag-bg").setAttribute("y", top);
  $("tag-bg").setAttribute("width", w);
  const text = $("tag-text");
  text.setAttribute("x", left + 1.2);
  text.setAttribute("y", top + 2.3);
}

function onGoal(d) {
  if (Array.isArray(d.score)) setScore(d.score);
  const name = TEAM_NAMES[d.team] || "";
  $("goal-line").textContent = (d.player ? d.player + " scores for " : "Goal for ") + name + " · " + scoreText(s.score);
  if (!$("pop").hidden) { // goal first, then the pop it interrupted
    $("pop").hidden = true;
    clearTimeout(timers.pop);
    s.pops.unshift(s.lastPop);
  }
  const el = $("goal");
  el.hidden = true; void el.offsetWidth; el.hidden = false; // restart the animation
  later("goal", GOAL_MS, () => { el.hidden = true; nextPop(); });
}

function onJump(d) {
  $("jump-title").textContent = d.title || "";
  $("jump-clock").textContent = d.clock ? "Match clock " + d.clock : "";
  $("jump").hidden = false;
  showTag(false);
  if (typeof d.clock === "string") $("clock").textContent = "LIVE " + d.clock;
  showStandby(); // a call never spans a jump
  setLive(false);
  later("jump", JUMP_MS, () => { $("jump").hidden = true; });
}

// ............................................................ calls

function showStandby() {
  if (s) s.call = null;
  $("standby").hidden = false;
  $("ask").hidden = true;
}

function optionButtons() {
  return Array.from(document.querySelectorAll("#ask .opt"));
}

const SUBS = { PASS: "to a teammate", SHOOT: "at goal", DRIBBLE: "run with it" };

function onCallOpen(d) {
  if (typeof d.call_id !== "string") return;
  clearTimeout(timers.result);
  if (s.tab === "fc") { // never let a live call go unseen
    setTab("live");
    s.switchedAt = Date.now(); // a tap meant for the forecast panel must not land on a call
  }
  s.call = { id: d.call_id, player: d.player || "the player", locked: false, result: false };
  const prompt = String(d.prompt || "What's the call?");
  const cut = prompt.lastIndexOf(". "); // context sentence(s), then the question
  $("call-ctx").textContent = cut > 0 ? prompt.slice(0, cut + 1) : "";
  $("call-q").textContent = cut > 0 ? prompt.slice(cut + 2) : prompt;
  $("call-team").textContent = (TEAM_NAMES[d.team] || "").toUpperCase();
  $("call-team").hidden = !TEAM_NAMES[d.team];
  setStatus("● OPEN · LOCKS WHEN " + s.call.player.toUpperCase() + " PLAYS IT", false);
  setLine("Call it before the ball moves.", false);
  $("call-explain").hidden = true;
  optionButtons().forEach((b) => {
    b.className = "opt";
    b.disabled = false;
    b.querySelector("small").textContent = SUBS[b.dataset.pick];
  });
  $("standby").hidden = true;
  $("ask").hidden = false;
}

optionButtons().forEach((btn) => {
  btn.onclick = () => {
    const call = s && s.call;
    if (!call || call.locked || s.picks[call.id]) return;
    if (Date.now() - (s.switchedAt || 0) < SWITCH_GUARD_MS) return;
    s.picks[call.id] = btn.dataset.pick;
    send({ type: "call.answer", data: { call_id: call.id, pick: btn.dataset.pick } });
    optionButtons().forEach((b) => {
      b.disabled = true;
      b.classList.add(b === btn ? "mine" : "faded");
    });
    btn.querySelector("small").textContent = "YOUR CALL";
    setStatus("LOCKED IN · WAITING FOR THE PLAY", false);
    setLine("Points land the moment it happens.", false);
  };
});

function onCallLocked(d) {
  const call = s.call;
  if (!call || call.id !== d.call_id) return;
  call.locked = true;
  optionButtons().forEach((b) => { b.disabled = true; });
  if (!s.picks[call.id]) setStatus("CALL CLOSED", true);
}

function onCallResult(d) {
  // Totals come from the server, so update them even if the card has moved on.
  if (typeof d.total === "number") setTotal(d.total, d.points);
  if (typeof d.streak === "number") setStreak(d.streak);
  showPop(d);
  const call = s.call;
  if (!call || call.id !== d.call_id) return;
  call.locked = true;
  markOptions(d.answer, d.pick);
  setStatus("CALL CLOSED", true);
  setLine(resultLine(d), true);
  const explain = typeof d.explain === "string" ? d.explain : "";
  $("call-explain").textContent = explain;
  $("call-explain").hidden = !explain;
  later("result", RESULT_HOLD_MS, showStandby);
}

function markOptions(answer, pick) {
  optionButtons().forEach((b) => {
    const p = b.dataset.pick, small = b.querySelector("small");
    b.disabled = true;
    b.className = "opt";
    if (p === answer) {
      b.classList.add("answer");
      small.textContent = p === pick ? "YOU CALLED IT" : "IT WAS THIS";
    } else if (p === pick) {
      b.classList.add("wrong");
      small.textContent = "YOUR CALL";
    } else {
      b.classList.add("faded");
    }
  });
}

function resultLine(d) {
  const answer = String(d.answer || "").toUpperCase();
  if (d.too_quick) return "Too quick! It was a " + answer + ". No points lost, your streak is safe.";
  if (d.correct) return d.streak >= 3 ? "On fire! " + d.streak + " right in a row." : "Nailed it. Keep the streak going.";
  return "So close. It was a " + answer + ". +10 just for playing.";
}

function setStatus(text, done) {
  $("call-status").textContent = text;
  $("call-status").classList.toggle("done", done);
}

function setLine(text, done) {
  $("call-line").textContent = text;
  $("call-line").classList.toggle("done", done);
}

// ............................................................ points

function setTotal(total, gained) {
  s.total = total;
  $("total").textContent = total.toLocaleString("en-US");
  if (gained > 0) {
    const gain = $("gain"), wrap = $("total-wrap");
    gain.textContent = "+" + fmt(gained);
    gain.hidden = true; void gain.offsetWidth; gain.hidden = false;
    wrap.classList.remove("bump"); void wrap.offsetWidth; wrap.classList.add("bump");
    later("gain", 1300, () => { gain.hidden = true; });
  }
}

function setStreak(streak) {
  s && (s.streak = streak);
  document.querySelectorAll("#pips i").forEach((pip, i) => pip.classList.toggle("on", i < streak));
  $("streak-hint").textContent = streak >= 3 ? "On fire: bonus on every right call" : "3 right in a row = bonus";
}

function ribbon(pts, text, color) {
  const div = document.createElement("div");
  div.className = "ribbon rib1";
  const a = document.createElement("span");
  a.style.color = color;
  a.textContent = pts;
  const b = document.createElement("span");
  b.textContent = text;
  div.append(a, b);
  return div;
}

// One pop at a time: later ones wait in s.pops (and all wait for the goal banner).
function showPop(d) {
  if (!$("goal").hidden || !$("pop").hidden) { s.pops.push(d); return; }
  s.lastPop = d;
  const pts = $("pop-pts"), list = $("pop-ribbons");
  list.textContent = "";
  pts.className = "pop-pts pop";
  if (d.fc) {
    if (d.points > 0) pts.classList.add("good");
    pts.textContent = "+" + fmt(d.points);
    $("pop-label").textContent = d.label;
    list.append(ribbon("+" + fmt(d.points), d.line, d.points > 0 ? "#00FF85" : "#CDB8D1"));
  } else if (d.too_quick) {
    pts.classList.add("quick");
    pts.textContent = "TOO QUICK!";
    $("pop-label").textContent = "";
    list.append(ribbon("+0", "IT WAS A " + String(d.answer || "").toUpperCase() + " · STREAK SAFE", "#CDB8D1"));
  } else if (d.correct) {
    pts.classList.add("good");
    pts.textContent = "+" + d.points;
    $("pop-label").textContent = "CORRECT CALL";
    list.append(ribbon("+10", "CALLED IT", "#EEDFF0"), ribbon("+100", "READ THE PLAY", "#00FF85"));
    if (d.streak_bonus > 0) list.append(ribbon("+" + d.streak_bonus, "STREAK x" + d.streak, "#00FF85"));
    if (navigator.vibrate) navigator.vibrate(30);
  } else {
    pts.textContent = "+" + (d.points || 0);
    $("pop-label").textContent = "CALLED IT";
    list.append(ribbon("+" + (d.points || 0), "TOOK PART", "#EEDFF0"));
  }
  const el = $("pop");
  el.hidden = true; void el.offsetWidth; el.hidden = false;
  later("pop", POP_MS, () => { el.hidden = true; nextPop(); });
}

function nextPop() {
  const d = s.pops.shift();
  if (d) showPop(d);
}

// ............................................................ forecasts

const FT_NAMES = { ars: "an Arsenal win", draw: "a draw", che: "a Chelsea win" };
const fmt = (n) => (Number(n) || 0).toLocaleString("en-US");
const resultOf = (hs, aw) => (hs > aw ? "ars" : hs === aw ? "draw" : "che");

function el(tag, cls, text) {
  const e = document.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function setTab(tab) {
  s.tab = tab;
  const fc = tab === "fc";
  $("tab-live").classList.toggle("on", !fc);
  $("tab-fc").classList.toggle("on", fc);
  $("rt").hidden = fc;
  $("streak").hidden = fc;
  $("fc").hidden = !fc;
  if (fc) $("tab-dot").hidden = true;
}
$("tab-live").onclick = $("fc-back").onclick = () => { if (s) setTab("live"); };
$("tab-fc").onclick = () => { if (s) setTab("fc"); };
$("ht-pick").onclick = () => { if (s) { closeSheet(); setTab("fc"); } };

// Window picks open on demo.clock and close on jumps, half time and full time.
function setLive(on) {
  s.fc.live = on;
  renderForecasts();
}

function settled() {
  if (s.tab !== "fc") $("tab-dot").hidden = false;
}

function optButton(o, onclick) {
  const b = el("button", "opt", String(o.label || ""));
  b.dataset.pick = o.id;
  b.onclick = onclick;
  return b;
}

function buildForecasts(info) {
  if (!info || !Array.isArray(info.windows) || !info.final) return;
  s.fc.info = info;
  s.fc.cards = {};
  $("fc-cards").textContent = "";
  info.windows.forEach((w) => {
    const card = el("div", "card fc-card");
    const top = el("div", "row-between"), left = el("div", "badges");
    left.append(el("span", "badge", w.label), el("span", "muted small fc-range"));
    top.append(left, el("span", "fc-pts", "+" + fmt(w.points)));
    const opts = el("div", "fc-opts");
    w.options.forEach((o) => opts.append(optButton(o, () => pickWindow(w.key, o.id))));
    card.append(top, el("div", "fc-q", w.question), opts, el("div", "fc-note"));
    $("fc-cards").append(card);
    s.fc.cards[w.key] = { def: w, el: card, state: "open", pick: null, range: "", res: null };
  });
  $("ft-pts").textContent = "+" + fmt(info.final.points);
  $("ft-exact").textContent = "+" + fmt(info.final.exact_points) + " extra if you nail it";
  $("ft-opts").textContent = "";
  info.final.options.forEach((o) => $("ft-opts").append(optButton(o, () => pickResult(o.id))));
  renderForecasts();
}

function pickWindow(key, pick) {
  const c = s.fc.cards[key];
  if (!c || c.state !== "open" || !s.fc.live) return;
  Object.assign(c, { state: "sending", pick: pick });
  send({ type: "forecast.pick", data: { key: key, pick: pick } });
  renderForecasts();
}

function onForecastLocked(d) {
  const c = s.fc.cards[d.key];
  if (!c) return;
  Object.assign(c, { state: "locked", pick: d.pick, range: d.from_clock + "–" + d.to_clock, to: d.to_clock });
  renderForecasts();
}

function onForecastRejected(d) {
  if (d.key === "final") return onFinalRejected(d);
  const c = s.fc.cards[d.key];
  if (c && c.state === "sending") Object.assign(c, { state: "open", pick: null });
  if (d.reason === "not_live") setLive(false); // "Opens when play resumes" until the next clock
  else renderForecasts();
}

function onForecastResult(d) {
  if (typeof d.total === "number") setTotal(d.total, d.points);
  s.fc.points += Number(d.points) || 0;
  settled();
  const c = s.fc.cards[d.key];
  showPop({ fc: true, points: d.points, label: c ? c.def.label : "FORECAST",
    line: d.correct ? "FORECAST HIT" : d.outcome !== "none" ? "NOT THIS TIME"
      : d.key === "f5" ? "EVEN SPLIT · NO POINTS LOST" : "NO SHOTS · NO POINTS LOST" });
  if (!c) return;
  Object.assign(c, { state: "result", pick: d.pick, res: d });
  renderForecasts();
  later("fc-" + d.key, RESULT_HOLD_MS, () => {
    Object.assign(c, { state: "open", pick: null, range: "", res: null });
    renderForecasts();
  });
}

// Full time: result and exact score are linked (design Forecast.dc.html pickResult / bump).
function pickResult(id) {
  const f = s.fc.final;
  if (f.locked || f.res || f.ft === id) return;
  let hs = f.hs, aw = f.aw;
  if (id === "ars" && hs <= aw) hs = aw + 1;
  if (id === "draw") hs = aw = Math.max(hs, aw);
  if (id === "che" && aw <= hs) aw = hs + 1;
  if (hs > 9 || aw > 9) return;
  Object.assign(f, { hs: hs, aw: aw });
  setFinal(id, "score");
}

function bump(field, step) {
  const f = s.fc.final;
  if (f.locked || f.res) return;
  const min = field === "hs" ? s.score[0] : s.score[1];
  f[field] = Math.max(min, Math.min(9, f[field] + step));
  const ft = resultOf(f.hs, f.aw);
  setFinal(ft, ft !== f.ft ? "result" : "");
}
[["hs-up", "hs", 1], ["hs-down", "hs", -1], ["aw-up", "aw", 1], ["aw-down", "aw", -1]].forEach(([id, field, step]) => {
  $(id).onclick = () => { if (s) bump(field, step); };
});

// Show the new pick now; send it once the taps settle. final_saved is what counts.
function setFinal(ft, flash) {
  const f = s.fc.final;
  Object.assign(f, { ft: ft, flash: flash });
  renderForecasts();
  later("final", 300, () => send({ type: "forecast.final", data: { result: f.ft, home: f.hs, away: f.aw } }));
}

function onFinalRejected(d) {
  const f = s.fc.final;
  if (d.reason === "locked") f.locked = true;
  else if (f.saved) Object.assign(f, { ft: f.saved.result, hs: f.saved.home, aw: f.saved.away, flash: "" });
  else Object.assign(f, { ft: null, hs: s.score[0], aw: s.score[1], flash: "" });
  renderForecasts();
}

function onFinalLocked(d) {
  const f = s.fc.final;
  clearTimeout(timers.final);
  f.locked = true;
  f.saved = d.result ? d : null;
  if (f.saved) Object.assign(f, { ft: d.result, hs: d.home, aw: d.away, flash: "" });
  else f.ft = null; // nothing reached the server before the lock
  renderForecasts();
}

function onFinalResult(d) {
  if (typeof d.total === "number") setTotal(d.total, d.points);
  s.fc.points += Number(d.points) || 0;
  s.fc.final.res = d;
  settled();
  showPop({ fc: true, points: d.points, label: "FULL TIME",
    line: d.exact_points > 0 ? "RESULT AND EXACT SCORE" : d.points > 0 ? "RESULT RIGHT" : "NOT THIS TIME" });
  renderForecasts();
}

function renderForecasts() {
  let riding = 0;
  Object.values(s.fc.cards).forEach((c) => {
    renderCard(c);
    if (c.state === "locked") riding += c.def.points;
  });
  const f = s.fc.final, info = s.fc.info;
  if (f.saved && !f.res && info) riding += info.final.points + info.final.exact_points;
  $("fc-potential").textContent = "+" + fmt(riding);
  renderFinal();
}

function setNote(node, text, cls) {
  node.textContent = text;
  node.className = "fc-note" + (cls ? " " + cls : "");
}

function renderCard(c) {
  c.el.classList.toggle("on", c.state !== "open");
  c.el.querySelector(".fc-range").textContent = c.range;
  c.el.querySelectorAll(".opt").forEach((b) => {
    const p = b.dataset.pick;
    b.className = "opt";
    b.disabled = c.state !== "open" || !s.fc.live;
    if (c.state === "open") return;
    if (c.state === "result" && p === c.res.outcome) b.classList.add("answer");
    else if (p === c.pick) b.classList.add(c.state === "result" ? "wrong" : "mine");
    else b.classList.add("faded");
  });
  const note = c.el.querySelector(".fc-note");
  if (c.state === "result") setNote(note, c.res.correct ? "Forecast hit +" + fmt(c.res.points) : String(c.res.text || ""), c.res.correct ? "hit" : "");
  else if (c.state === "locked") setNote(note, "LOCKED IN · SCORED AT " + c.to, "lock");
  else if (c.state === "sending") setNote(note, "Locking in…", "");
  else if (!s.fc.live) setNote(note, "Opens when play resumes", "");
  else setNote(note, "Scored over the next " + c.def.minutes + " match minutes.", "");
}

function finalText(r) {
  const opt = s.fc.info && s.fc.info.final.options.find((o) => o.id === r.result);
  return (opt ? opt.label : "") + " " + r.home + " – " + r.away;
}

function renderFinal() {
  const f = s.fc.final, off = f.locked || Boolean(f.res);
  const answer = f.res && Array.isArray(f.res.final) ? resultOf(f.res.final[0], f.res.final[1]) : null;
  $("ft-card").classList.toggle("on", Boolean(f.ft));
  $("ft-opts").querySelectorAll(".opt").forEach((b) => {
    const p = b.dataset.pick;
    b.disabled = off;
    b.className = "opt" + (p === answer ? " answer" : p === f.ft ? (answer ? " wrong" : " mine") : f.ft ? " faded" : "");
  });
  $("hs").textContent = f.hs;
  $("aw").textContent = f.aw;
  $("hs-up").disabled = off || f.hs >= 9;
  $("aw-up").disabled = off || f.aw >= 9;
  $("hs-down").disabled = off || f.hs <= s.score[0];
  $("aw-down").disabled = off || f.aw <= s.score[1];
  $("steppers").classList.toggle("off", !f.ft);
  const note = $("ft-note");
  if (f.res) {
    const fin = (f.res.final || []).join(" – ");
    setNote(note, f.res.points > 0 ? "Forecast hit +" + fmt(f.res.points) + (f.res.exact_points > 0 ? ", exact score too!" : "")
      : "Not this time. It ended " + fin + ".", f.res.points > 0 ? "hit" : "");
  } else if (f.locked) {
    setNote(note, "LOCKED AT HALF TIME" + (f.saved ? " · " + finalText(f.saved) : " · NO PICK"), "lock");
  } else if (!f.ft) {
    setNote(note, "Pick a result or set a score. Each one updates the other.", "");
  } else {
    const hint = f.flash === "result" ? "Your score means " + FT_NAMES[f.ft] + ", so we set that as your result."
      : f.flash === "score" ? "We set a score that fits " + FT_NAMES[f.ft] + ". Adjust it if you like."
        : "Result and exact score stay in step.";
    setNote(note, hint + (f.saved ? " Saved: " + finalText(f.saved) + "." : ""), f.flash ? "flash" : "");
  }
}

// ............................................................ breaks and errors

function onBreak(kind, d) {
  if (Array.isArray(d.score)) setScore(d.score);
  showStandby();
  setLive(false);
  if (typeof d.total === "number") setTotal(d.total, 0);
  if (d.saved === true) s.save.saved = true;
  if (kind === "full") return onFullTime(d);
  $("clock").textContent = "HALF TIME";
  Object.assign(s, { inBreak: true, htSeen: true, ht: d });
  if (!s.save.saved) openSheet("half");
}

// ............................................................ save sheet (half time, HUD chip, full time)

const AV_CLASS = { Mia: "mia", Dan: "dan", Leo: "leo" };

function avatar(p) {
  const name = String(p.name || "?");
  return el("span", "av " + (p.me ? "me" : AV_CLASS[name] || ""), p.me ? "YOU" : name.charAt(0).toUpperCase());
}

function roomPlayers(d) {
  const room = d && d.room;
  if (!room || typeof room.code !== "string" || !Array.isArray(room.players)) return null;
  const players = room.players.filter((p) => p && typeof p.name === "string");
  return players.length ? { code: room.code, players: players } : null;
}

function joinNames(names) {
  return names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

// "You lead Room 4K7Q. Mia and Dan already saved theirs." (global constraints room line rule)
function renderRoomLine(d) {
  const room = roomPlayers(d);
  $("sheet-room").hidden = !room;
  if (!room) return;
  const rank = d.you && typeof d.you.rank === "number" ? d.you.rank : null;
  const youLead = rank !== null ? rank === 1 : Boolean(room.players[0].me);
  const leader = room.players.find((p) => !p.me) || room.players[0];
  $("sheet-room-lead").textContent = (youLead ? "You lead" : leader.name + " leads") + " Room " + room.code + ".";
  const savers = room.players.filter((p) => !p.me && p.saved === true).map((p) => p.name);
  $("sheet-room-saved").textContent = savers.length ? joinNames(savers) + " already saved theirs." : "";
  $("sheet-avatars").textContent = "";
  room.players.forEach((p) => $("sheet-avatars").append(avatar(p)));
}

// kind: "half" (half-time sheet), "chip" (from the HUD chip), "full" (from the full-time screen)
function openSheet(kind) {
  clearTimeout(timers.sheet);
  s.sheet = kind;
  const half = kind === "half", d = s.ht || {};
  $("sheet-pts").textContent = fmt(s.total);
  $("sheet-pts-label").textContent = half ? "PTS AT THE BREAK" : "PTS WAITING TO BE SAVED";
  $("sheet-t1").textContent = half ? "Great first half." : "Keep your points.";
  $("sheet-t2").textContent = half ? "Save your progress." : "Climb the season.";
  const secs = Math.round(Number(d.seconds));
  $("sheet-clock").textContent = half ? (secs > 0 ? "Second half in " + secs + " s" : "Second half soon") : "";
  if (half) renderRoomLine(d);
  else $("sheet-room").hidden = true;
  const b = d.breakdown || {};
  $("sheet-fc").textContent = (typeof b.calls === "number" ? "Calls: +" + fmt(b.calls) + " · " : "")
    + "Forecasts: +" + fmt(typeof b.forecasts === "number" ? b.forecasts : s.fc.points);
  $("sheet-fc").hidden = !half;
  $("ht-pick").hidden = !half || s.fc.final.locked; // the full-time pick stays open until half 2
  $("save-btn").textContent = half ? "SAVE MY PROGRESS" : "SAVE MY POINTS";
  $("sheet-later").textContent = kind === "full" ? "Not now" : "Maybe later";
  $("done-close").textContent = kind === "full" ? "BACK TO MY SCORE" : "BACK TO THE MATCH";
  if (s.save.saved) showDone(s.save.fresh);
  showSaveView();
  ["sheet", "sheet-dim"].forEach((id) => {
    const node = $(id);
    node.classList.remove("out");
    node.hidden = true; void node.offsetWidth; node.hidden = false; // replay the slide-up
  });
  $("save-chip").hidden = true;
}

function showSaveView() {
  $("save-form").hidden = s.save.saved;
  $("save-done").hidden = !s.save.saved;
}

// Slide the sheet out; the chip stays in the HUD until the points are saved.
function closeSheet(now) {
  if (!s.sheet) return;
  s.sheet = null;
  const hide = () => { $("sheet").hidden = true; $("sheet-dim").hidden = true; };
  if (now) { clearTimeout(timers.sheet); hide(); }
  else {
    $("sheet").classList.add("out");
    $("sheet-dim").classList.add("out");
    later("sheet", 240, hide);
  }
  updateChip();
}

function updateChip() {
  $("save-chip").hidden = !s.htSeen || s.save.saved || s.finished || Boolean(s.sheet);
}

$("sheet-later").onclick = $("sheet-dim").onclick = $("done-close").onclick = () => { if (s) closeSheet(); };
$("save-chip").onclick = () => { if (s) openSheet("chip"); };
$("email").oninput = () => setEmailError("");

function setEmailError(text) {
  $("email-error").textContent = text;
  $("email-error").hidden = !text;
  $("email").classList.toggle("bad", Boolean(text));
}

function setSaveBusy(busy) {
  $("save-btn").disabled = busy;
  if (busy) $("save-btn").textContent = "SAVING…";
  else if (s && s.sheet) $("save-btn").textContent = s.sheet === "half" ? "SAVE MY PROGRESS" : "SAVE MY POINTS";
}

// One signup in flight at a time; the server adds the +500 once.
$("save-form").onsubmit = (ev) => {
  ev.preventDefault();
  if (!s || s.save.saved || s.save.pending) return;
  const email = $("email").value.trim();
  if (email.length > 254 || !EMAIL_RE.test(email)) return setEmailError("That email doesn't look right. Mind checking it?");
  if (!ws || ws.readyState !== WebSocket.OPEN) return setEmailError("The match has closed, so we can't save now. Play again to save.");
  Object.assign(s.save, { pending: true, email: email });
  setSaveBusy(true);
  send({ type: "signup", data: { email: email } });
  later("signup", SIGNUP_WAIT_MS, () => {
    s.save.pending = false;
    setSaveBusy(false);
    setEmailError("No answer from the match. Try again.");
  });
};

function onSignupSaved(d) {
  clearTimeout(timers.signup);
  const bonus = Number(d.bonus) || 0;
  Object.assign(s.save, { saved: true, pending: false, bonus: bonus, fresh: true });
  setSaveBusy(false);
  if (typeof d.total === "number") setTotal(d.total, bonus);
  showDone(true);
}

function onSignupRejected(d) {
  clearTimeout(timers.signup);
  s.save.pending = false;
  setSaveBusy(false);
  if (d.reason === "already") {
    s.save.saved = true;
    showDone(false);
  } else {
    setEmailError("That email doesn't look right. Mind checking it?");
  }
}

// "+500 PROGRESS SAVED" with the honest demo line, then refresh everything that shows the total.
function showDone(fresh) {
  const bonus = s.save.bonus;
  $("done-bonus").textContent = "+" + fmt(bonus);
  $("done-bonus").hidden = !fresh || bonus <= 0;
  $("done-label").textContent = fresh ? "PROGRESS SAVED" : "ALREADY SAVED";
  const line = $("done-line");
  line.textContent = "";
  line.append("Your " + fmt(s.total) + " points are safe" + (fresh && bonus > 0 ? ", plus a welcome bonus. " : ". "));
  if (s.save.email) line.append("We'd send a sign-in link to ", el("b", "", s.save.email), ". ");
  line.append("(Demo: nothing is sent.)");
  if (s.sheet) showSaveView();
  updateChip();
  if (s.ft) renderFtSave();
}

// ............................................................ full time (design Result.dc.html)

const ROW_MSG = { rt: "COUNTING REAL-TIME CALLS…", fc: "COUNTING YOUR FORECASTS…", pb: "AND YOUR PLAYERS' BONUS…" };
const BADGE_CLASS = { "ORACLE": "g", "ON FIRE": "c", "GOOD EYE": "p" };

function onFullTime(d) {
  s.finished = true;
  clearTimeout(timers.silence); // the server keeps the socket open for a late save, then closes it
  $("clock").textContent = "FULL TIME";
  closeSheet(true);
  $("save-chip").hidden = true;
  const rows = [];
  (Array.isArray(d.rows) ? d.rows : []).forEach((r) => {
    const node = r && ROW_MSG.hasOwnProperty(r.key) ? document.querySelector('.ft-row[data-key="' + r.key + '"]') : null;
    if (!node || rows.some((x) => x.node === node)) return;
    node.querySelector(".ft-row-label").textContent = String(r.label || "");
    node.querySelector(".ft-row-detail").textContent = String(r.detail || "");
    rows.push({ key: r.key, node: node, points: Math.max(0, Math.round(Number(r.points) || 0)), value: 0, landed: false });
  });
  document.querySelectorAll(".ft-row").forEach((node) => {
    node.hidden = !rows.some((r) => r.node === node);
    node.className = "ft-row";
    node.querySelector(".ft-val").textContent = "—";
    node.querySelector(".ft-bar i").style.width = "0";
  });
  const sum = rows.reduce((a, r) => a + r.points, 0);
  s.ft = { stage: "idle", rows: rows, raf: 0, total: typeof d.total === "number" ? d.total : sum, me: null };
  $("ft-score").textContent = scoreText(s.score);
  $("ft-stamp").textContent = typeof d.stamp === "string" ? d.stamp : "";
  buildBadges(d.badges);
  buildRoom(d);
  $("ft").classList.remove("fast", "done");
  $("ft-scroll").scrollTop = 0;
  setFtStage("idle");
  $("ft").hidden = false;
}

function buildBadges(badges) {
  const box = $("ft-badges");
  box.textContent = "";
  (Array.isArray(badges) ? badges : []).forEach((b) => {
    if (!b || typeof b.title !== "string") return;
    const n = box.children.length;
    const card = el("div", "ft-badge " + (BADGE_CLASS[b.title] || ["g", "c", "p"][n % 3]));
    card.style.setProperty("--i", n);
    card.append(el("b", "", b.title), el("span", "", String(b.text || "")));
    box.append(card);
  });
}

function buildRoom(d) {
  const room = roomPlayers(d), list = $("ft-room-list");
  list.textContent = "";
  s.ft.hasRoom = Boolean(room);
  if (!room) return;
  $("ft-room-code").textContent = "ROOM " + room.code;
  const friends = room.players.filter((p) => !p.me).length;
  $("ft-room-n").textContent = friends + (friends === 1 ? " friend" : " friends") + " played";
  room.players.forEach((p, i) => {
    const row = el("div", "ft-room-row" + (p.me ? " me" : ""));
    row.style.setProperty("--i", i);
    const pts = el("span", "pts", fmt(p.pts));
    row.append(el("span", "pos", String(i + 1)), avatar(p), el("span", "name", p.me ? "You" : p.name), pts);
    if (p.me) s.ft.me = pts;
    list.append(row);
  });
}

// idle -> counting -> done; each stage shows only its own controls.
function setFtStage(stage) {
  const ft = s.ft;
  ft.stage = stage;
  const idle = stage === "idle", done = stage === "done";
  $("ft-count").hidden = !idle;
  $("ft-counting").hidden = $("ft-skip").hidden = stage !== "counting";
  $("ft-rows").classList.toggle("counting", stage === "counting");
  $("ft").classList.toggle("done", done);
  ["ft-save-note", "ft-save"].forEach((id) => { $(id).hidden = !done; });
  $("ft-badges").hidden = !done || !$("ft-badges").children.length;
  $("ft-room").hidden = !done || !ft.hasRoom;
  $("ft-stamp").hidden = !done || !$("ft-stamp").textContent;
  $("ft-message").hidden = done;
  $("ft-message").classList.toggle("blink", stage === "counting");
  if (idle) $("ft-message").textContent = "READY WHEN YOU ARE";
  const total = $("ft-total");
  total.className = "ft-total" + (idle ? " idle" : "");
  total.textContent = idle ? "?" : fmt(rowsSum(ft));
  if (done) {
    total.textContent = fmt(ft.total);
    void total.offsetWidth;
    total.classList.add("pop");
    renderFtSave();
  }
}

function rowsSum(ft) {
  return ft.rows.reduce((a, r) => a + r.value, 0);
}

function paintRow(r, value, progress) {
  r.value = value;
  r.node.querySelector(".ft-val").textContent = "+" + fmt(value);
  r.node.querySelector(".ft-bar i").style.width = (r.points > 0 ? progress * 100 : 0) + "%";
}

function landRow(r) {
  r.landed = true;
  r.node.classList.remove("active");
  r.node.classList.add("started", "landed");
}

$("ft-count").onclick = () => {
  const ft = s && s.ft;
  if (!ft || ft.stage !== "idle") return;
  setFtStage("counting");
  countRow(ft, 0);
};

// Rows count up one after another (2.8 s each, ease-out), then the stamp, badges and room.
function countRow(ft, i) {
  if (s.ft !== ft || ft.stage !== "counting") return;
  const r = ft.rows[i];
  if (!r) {
    $("ft-message").textContent = "";
    later("ft", 650, () => { if (s.ft === ft && ft.stage === "counting") finishCount(ft, false); });
    return;
  }
  r.node.classList.add("started", "active");
  $("ft-message").textContent = ROW_MSG[r.key] || "COUNTING…";
  const dur = r.points > 0 ? ROW_MS : ZERO_ROW_MS;
  let t0 = null;
  const frame = (now) => {
    if (s.ft !== ft || ft.stage !== "counting") return;
    if (t0 === null) t0 = now;
    const t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 4);
    paintRow(r, t >= 1 ? r.points : Math.round(r.points * e), e);
    $("ft-total").textContent = fmt(rowsSum(ft));
    if (t < 1) { ft.raf = requestAnimationFrame(frame); return; }
    landRow(r);
    if (i + 1 < ft.rows.length) later("ft", 500, () => countRow(ft, i + 1));
    else countRow(ft, i + 1);
  };
  ft.raf = requestAnimationFrame(frame);
}

$("ft-skip").onclick = () => {
  const ft = s && s.ft;
  if (!ft || ft.stage !== "counting") return;
  finishCount(ft, true);
};

function finishCount(ft, fast) {
  cancelAnimationFrame(ft.raf);
  clearTimeout(timers.ft);
  ft.rows.forEach((r) => { if (!r.landed) { paintRow(r, r.points, 1); landRow(r); } });
  $("ft").classList.toggle("fast", fast);
  setFtStage("done");
  later("ft", (fast ? 0.4 : 1) * 2000, () => {
    const box = $("ft-scroll");
    try { box.scrollTo({ top: box.scrollHeight, behavior: "smooth" }); } catch (e) { box.scrollTop = box.scrollHeight; }
  });
}

function renderFtSave() {
  const ft = s.ft;
  if (ft.stage === "done" && typeof s.total === "number" && s.save.saved) {
    ft.total = Math.max(ft.total, s.total); // a save after full time adds the welcome bonus
    $("ft-total").textContent = fmt(ft.total);
    if (ft.me) ft.me.textContent = fmt(ft.total);
  }
  $("ft-save").textContent = s.save.saved ? "POINTS SAVED · " + fmt(ft.total) : "SAVE MY " + fmt(ft.total) + " POINTS";
}

$("ft-save").onclick = () => { if (s && s.ft) openSheet("full"); };

function connectionLost(code) {
  clearTimers();
  closeSheet(true);
  $("save-chip").hidden = true;
  const line = code === 1013
    ? "Lots of people are playing right now. Try again in a moment."
    : "We lost the connection to the match.";
  openModal("CONNECTION LOST", line, true);
}

function openModal(kicker, line, again) {
  $("modal-kicker").textContent = kicker;
  $("modal-score").textContent = scoreText(s.score);
  $("modal-total").textContent = s.total.toLocaleString("en-US");
  $("modal-line").textContent = line;
  $("again").hidden = !again;
  $("modal").hidden = false;
}
